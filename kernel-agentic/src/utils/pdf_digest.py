import argparse
import datetime
import logging
import time
import re
from pathlib import Path
import pandas as pd
import uuid
import itertools, textwrap
# ── Docling ────────────────────────────────────────────────────────────────
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, smolvlm_picture_description
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.utils.export import generate_multimodal_pages
from docling.utils.utils import create_hash
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline  # noqa: F401 – keeps plugin alive

from docling_core.types.doc import (
    ImageRefMode,
    PictureItem,
    TableItem,
)

try:
    from docling_core.types.doc import FormulaItem
except ImportError:
    from docling_core.types.doc import EquationItem as FormulaItem
try:
    from docling_core.types.doc import CodeBlockItem
except ImportError:
    from docling_core.types.doc import CodeItem as CodeBlockItem

import os 
import sys 
from pathlib import Path
dir_path=str(Path(os.path.dirname(Path(__file__))).parent)
if dir_path not in sys.path:
    sys.path.append(dir_path)

from agents.explainer_agents import (
    EquationExplainer,
    CodeSnippetExplainer,
    TableExplainer,
    PictureExplainer
)

_log = logging.getLogger(__name__)
IMAGE_RESOLUTION_SCALE = 2.0

def improve_picture_caption(element, orig_caption: str) -> str:
    """If the auto caption looks short or generic, ask GPT-4o-mini for more detail."""
    MIN_TOKENS = 40
    
    if orig_caption=='':
        good_enough=False
    else:
        orig_caption=orig_caption[0].text
        good_enough = len(orig_caption.split()) >= MIN_TOKENS or "axis" in orig_caption.lower()
    if good_enough:
        return orig_caption

    # Grab the built-in data URI (base64) that Docling already generated
    data_uri = str(element.image.uri)
    # Ask the PictureExplainer using that URI directly
    return PictureExplainer().explain(data_uri)

def _collect_explanations(doc):
    """
    Collect explanations for equations, code blocks, and tables
    Returns a dict mapping element IDs to explanations
    """
    eq_agent = EquationExplainer()
    code_agent = CodeSnippetExplainer()
    tbl_agent = TableExplainer()
    
    explanations = {}
    
    for element, _ in doc.iterate_items():
        element_id = getattr(element, 'self_ref', None) or f"element_{id(element)}"
        
        if isinstance(element, FormulaItem):
            latex = getattr(element, "get_latex", lambda: element.text)()
            expl = eq_agent.explain(latex).strip()
            explanations[element_id] = f"**Following equation describes:** {expl}"
            
        elif isinstance(element, CodeBlockItem):
            src = getattr(element, "code_text", None) or getattr(element, "text", "")
            expl = code_agent.explain(src).strip()
            explanations[element_id] = f"**Following code does:** {expl}"
            
        elif isinstance(element, TableItem):
            try:
                csv_preview = element.export_to_dataframe().head(8).to_csv(index=False)
            except Exception:
                csv_preview = "<unavailable>"
            expl = tbl_agent.explain(csv_preview).strip()
            explanations[element_id] = f"**Following table contains:** {expl}"
        elif isinstance(element, PictureItem):
            auto_caption = element.annotations or ""
            caption = improve_picture_caption(element, auto_caption)
            explanations[element_id] = f"**Image description:** {caption}"
    
    return explanations

def _inject_explanations_into_markdown(
    md_content: str,
    explanations: dict[str, str],
) -> str:
    # keep document order → then bucket by type
    ordered = [explanations[k] for k in sorted(explanations, key=lambda x: str(x))]
    eq_iter, code_iter, tbl_iter, pic_iter = (
        (e for e in ordered if e.startswith("**Following equation")),
        (e for e in ordered if e.startswith("**Following code")),
        (e for e in ordered if e.startswith("**Following table")),
        (e for e in ordered if e.startswith("**Image description")),
    )

    def inject(pattern: str, iterator) -> None:
        nonlocal md_content

        def _repl(match):
            try:
                return f"{next(iterator)}\n\n{match.group(0)}"
            except StopIteration:
                return match.group(0)

        md_content = re.sub(pattern, _repl, md_content,
                            flags=re.DOTALL | re.MULTILINE)

    # code blocks  ``` … ```   or  <pre> … </pre>
    inject(r"(?:^\s*```[\w+-]*\s[\s\S]*?```|<pre[\s\S]*?</pre>)", code_iter)
    # markdown tables or HTML tables
    inject(r"(?:^\s*\|.*\n\|(?:[-:| ]+)\n(?:.*\n)+?(?=\n[^|]|$)|<table[\s\S]*?</table>)", tbl_iter)
    # equations  $$ … $$   \[ … \]   or inline $ … $
    inject(r"(?:\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|(?<!\$)\$[^\$]+?\$(?!\$))", eq_iter)
    # pictures   ![alt](url)   or HTML <img …>
    inject(r"(?:!\[[^\]]*]\([^)]+\)|<img[^>]*>)", pic_iter)

    return md_content

def _inject_explanations_into_html(
    html_content: str,
    explanations: dict[str, str],
) -> str:
    ordered = [explanations[k] for k in sorted(explanations, key=lambda x: str(x))]
    eq_iter, code_iter, tbl_iter, pic_iter = (
        (e for e in ordered if e.startswith("**Following equation")),
        (e for e in ordered if e.startswith("**Following code")),
        (e for e in ordered if e.startswith("**Following table")),
        (e for e in ordered if e.startswith("**Image description")),
    )

    def inject(pattern: str, iterator) -> None:
        nonlocal html_content

        def _repl(match):
            try:
                expl = next(iterator)
                return f'<p><strong>{expl}</strong></p>\n{match.group(0)}'
            except StopIteration:
                return match.group(0)

        html_content = re.sub(pattern, _repl, html_content,
                              flags=re.DOTALL | re.IGNORECASE)

    inject(r"<pre[\s\S]*?</pre>",   code_iter)   # code
    inject(r"<table[\s\S]*?</table>", tbl_iter)  # tables
    inject(r"<span[^>]*class=\"[^\"]*equation[^\"]*\"[^>]*>[\s\S]*?</span>", eq_iter)  # equations
    inject(r"<img[^>]*>", pic_iter)  # pictures (covers <img …> and <img/>)

    return html_content

def main(pdf_path: str, output_dir: str):
    logging.basicConfig(level=logging.INFO)
    _log.info(f"Converting PDF: {pdf_path} → multimodal pages")

    pdf_path   = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    opts = PdfPipelineOptions(
        images_scale=IMAGE_RESOLUTION_SCALE,
        generate_page_images=True,
        generate_picture_images=True,
        do_picture_description=True,
        picture_description_options=smolvlm_picture_description,
        do_code_enrichment=True,
        do_formula_enrichment=True,
        do_picture_classification=False,
    )
    opts.picture_description_options.prompt = (
        "Describe the image in details around what data represents and axis. Be concise and accurate."
    )

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )

    t0 = time.time()
    conv_res = converter.convert(pdf_path)
    doc      = conv_res.document

    _log.info("Collecting explanations for elements...")
    explanations = _collect_explanations(doc)

    # --- parquet export (unchanged) ---------------------------------------
    rows = []
    for content_text, content_md, content_dt, page_cells, page_segments, page in generate_multimodal_pages(conv_res):
        dpi = page._default_image_scale * 72
        rows.append(
            dict(
                document = conv_res.input.file.name,
                hash     = conv_res.input.document_hash,
                page_hash= create_hash(f"{conv_res.input.document_hash}:{page.page_no-1}"),
                image    = dict(width=page.image.width, height=page.image.height, bytes=page.image.tobytes()),
                cells    = page_cells,
                contents = content_text,
                contents_md=content_md,
                contents_dt=content_dt,
                segments = page_segments,
                extra    = dict(page_num=page.page_no + 1,
                                width_in_points=page.size.width,
                                height_in_points=page.size.height,
                                dpi=dpi),
            )
        )

    df_result   = pd.json_normalize(rows)

    doc_stem = conv_res.input.file.stem

    # --- Enhanced Markdown & HTML with explanations ----------------------
    _log.info("Generating enhanced Markdown and HTML with explanations...")
    
    for mode, suffix in ((ImageRefMode.EMBEDDED, "with-images"),
                         (ImageRefMode.REFERENCED, "with-image-refs")):
        md_file = output_dir / f"{doc_stem}-{suffix}.md"
        
        # Generate original markdown
        doc.save_as_markdown(md_file, image_mode=mode)
        
        # Read and enhance with explanations
        with md_file.open('r', encoding='utf-8') as f:
            md_content = f.read()
        enhanced_md = _inject_explanations_into_markdown(md_content, explanations)
        
        # Save enhanced version
        enhanced_md_file = output_dir / f"{doc_stem}-{suffix}-enhanced.md"
        with enhanced_md_file.open('w', encoding='utf-8') as f:
            f.write(enhanced_md)
        
        _log.info(f"Saved enhanced Markdown → {enhanced_md_file}")

    # Enhanced HTML
    html_file = output_dir / f"{doc_stem}-with-image-refs.html"
    doc.save_as_html(html_file, image_mode=ImageRefMode.REFERENCED)
    
    # Read and enhance HTML
    with html_file.open('r', encoding='utf-8') as f:
        html_content = f.read()
    
    enhanced_html = _inject_explanations_into_html(html_content, explanations)
    
    # Save enhanced version
    enhanced_html_file = output_dir / f"{doc_stem}-with-image-refs-enhanced.html"
    with enhanced_html_file.open('w', encoding='utf-8') as f:
        f.write(enhanced_html)
    
    _log.info(f"Saved enhanced HTML → {enhanced_html_file}")

    _log.info(f"Completed in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate multimodal pages from a PDF.")
    parser.add_argument("--pdf_path",  type=str, default="p2.pdf")
    parser.add_argument("--output_dir", type=str, default="scratch")
    args = parser.parse_args()
    main(args.pdf_path, args.output_dir)