import argparse
import pandas as pd
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Preformatted,
    HRFlowable
)
from reportlab.lib import colors


def main(csv_path: str, pdf_path: str):
    # ------------------------------------------------------------------ #
    # 1) Read the CSV
    # ------------------------------------------------------------------ #
    df = pd.read_csv(csv_path)

    # ------------------------------------------------------------------ #
    # 2) Page setup  (LANDSCAPE – unchanged)
    # ------------------------------------------------------------------ #
    left_margin = right_margin = 0.5 * inch
    top_margin  = bottom_margin = 0.5 * inch
    gutter      = 0.25 * inch                      # space between columns

    doc = BaseDocTemplate(
        pdf_path,
        pagesize=landscape(letter),                # 792 pt × 612 pt
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin
    )

    # ------------------------------------------------------------------ #
    # 3) Code style + striped variants  (unchanged)
    # ------------------------------------------------------------------ #
    base_code = ParagraphStyle(
        name='Code',
        fontName='Courier',
        fontSize=6,
        leading=7
    )
    shade_even = ParagraphStyle(name='CodeEven', parent=base_code,
                                backColor=colors.whitesmoke)
    shade_odd  = base_code

    # ------------------------------------------------------------------ #
    # 4) Column geometry  (unchanged)
    # ------------------------------------------------------------------ #
    page_width, page_height = landscape(letter)
    usable_width  = page_width  - left_margin - right_margin
    usable_height = page_height - top_margin  - bottom_margin
    col_width     = (usable_width - gutter) / 2

    # ------------------------------------------------------------------ #
    # 5) Define the two side-by-side frames  (unchanged)
    # ------------------------------------------------------------------ #
    frame1 = Frame(
        left_margin,
        bottom_margin,
        col_width,
        usable_height,
        id='col1'
    )
    frame2 = Frame(
        left_margin + col_width + gutter,
        bottom_margin,
        col_width,
        usable_height,
        id='col2'
    )

    # ------------------------------------------------------------------ #
    # 6) Header + centre divider on every page  (unchanged)
    # ------------------------------------------------------------------ #
    def draw_header(canvas, doc):
        canvas.saveState()
        canvas.setFont('Courier', 6)

        header_y = page_height - top_margin + 2
        canvas.drawString(left_margin,                       header_y, 'ground_truth')
        canvas.drawString(left_margin + col_width + gutter,  header_y, 'LORA')

        # vertical divider
        x_sep = left_margin + col_width + gutter / 2
        canvas.setLineWidth(0.5)
        canvas.setStrokeColor(colors.grey)
        canvas.line(x_sep, bottom_margin, x_sep, page_height - top_margin)
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id='TwoCol', frames=[frame1, frame2], onPage=draw_header)
    ])

    # ------------------------------------------------------------------ #
    # 7) Build flowables with height-equalised pairs  ★ NEW ★
    # ------------------------------------------------------------------ #
    flowables = []
    make_rule = lambda: HRFlowable(width=col_width, thickness=0.4,
                                   color=colors.grey, spaceBefore=1, spaceAfter=1)

    for idx, row in df.iterrows():
        style = shade_even if idx % 2 == 0 else shade_odd

        gt_text   = str(row['ground_truth'])
        lora_text = str(row['LORA'])

        # --- Pad shorter side with blank lines so heights match -------
        gt_lines   = gt_text.split('\n')
        lora_lines = lora_text.split('\n')
        max_len    = max(len(gt_lines), len(lora_lines))

        if len(gt_lines)   < max_len:
            gt_text   += '\n' * (max_len - len(gt_lines))
        if len(lora_lines) < max_len:
            lora_text += '\n' * (max_len - len(lora_lines))

        # --- Add the pair + separator rule ---------------------------
        flowables.append(Preformatted(gt_text,   style))
        flowables.append(Preformatted(lora_text, style))
        flowables.append(make_rule())

    # ------------------------------------------------------------------ #
    # 8) Generate the PDF  (unchanged)
    # ------------------------------------------------------------------ #
    doc.build(flowables)
    print(f"PDF generated at: {pdf_path}")


# ---------------------------------------------------------------------- #
# CLI wrapper
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a striped two-column PDF (landscape) from a CSV, "
                    "with perfectly aligned rows."
    )
    parser.add_argument("--csv_path", default="lora_inference.csv",
                        help="Path to the input CSV file")
    parser.add_argument("--pdf_path", default="report.pdf",
                        help="Path to the output PDF file")
    args = parser.parse_args()
    main(csv_path=args.csv_path, pdf_path=args.pdf_path)
