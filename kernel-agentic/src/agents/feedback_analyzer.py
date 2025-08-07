from .base_agent import BaseAgent
import re
import json


class KernelFeedbackAnalyser(BaseAgent):
    def __init__(self):
        super().__init__("kernel_feedback_analyser",
            """You are a senior GPU kernel optimization and debugging expert with extensive experience in 
            HIP, CUDA, and Triton kernel development. Your expertise includes performance analysis, 
            memory optimization, and numerical debugging.

            **Your Task:**
            Analyze kernel code and execution feedback to provide actionable, specific guidance for improvement.
            
            **Analysis Framework:**
            1. **Error Classification**: Categorize the issue type and severity
            2. **Root Cause Analysis**: Identify the fundamental cause, not just symptoms  
            3. **Targeted Solutions**: Provide specific, implementable fixes
            4. **Optimization Opportunities**: Suggest performance improvements when applicable
            5. **Prevention Strategies**: Recommend approaches to avoid similar issues

            **Output Requirements:**
            Provide a comprehensive analysis with the following sections:
            - **Issue Classification**: error/performance/correctness/general
            - **Root Cause**: The fundamental problem causing the issue
            - **Immediate Fixes**: Specific code changes needed to resolve the problem
            - **Performance Impact**: How the issue affects kernel performance
            - **Optimization Suggestions**: Additional improvements beyond just fixing the issue
            - **Code Locations**: Specific lines or sections that need attention
            - **Testing Strategy**: How to validate the fixes and prevent regression

            **Technical Focus Areas:**
            - Memory access patterns and coalescing
            - Thread synchronization and race conditions  
            - Numerical stability and precision
            - Algorithm efficiency and complexity
            - Architecture-specific optimizations
            - Compiler optimization hints and pragmas

            Return a structured analysis that enables rapid issue resolution and performance improvement.
            """
        )

    def classify_issue_type(self, feedback_text: str) -> str:
        """Enhanced issue classification with more granular categories"""
        feedback_lower = feedback_text.lower()
        
        # Compilation errors (highest priority)
        if any(keyword in feedback_lower for keyword in [
            "error:", "compilation error", "syntax error", "undefined", 
            "undeclared", "expected", "missing", "invalid syntax"
        ]):
            return "compilation_error"
            
        # Runtime errors (second priority)
        if any(keyword in feedback_lower for keyword in [
            "segmentation fault", "segfault", "memory access", "cuda error",
            "hip error", "runtime error", "exception", "abort", "crash",
            "illegal memory access", "invalid device pointer"
        ]):
            return "runtime_error"
            
        # Correctness issues (third priority)  
        if any(keyword in feedback_lower for keyword in [
            "incorrect result", "wrong output", "mismatch", "nan", "inf",
            "numerical error", "precision", "accuracy", "max_abs_err"
        ]):
            return "correctness_issue"
            
        # Performance issues (fourth priority)
        if any(keyword in feedback_lower for keyword in [
            "slow", "performance", "latency", "throughput", "bandwidth",
            "utilization", "occupancy", "efficiency", "optimization"
        ]):
            return "performance_issue"
            
        return "general"

    def extract_error_details(self, feedback_text: str) -> dict:
        """Extract specific error details from feedback"""
        details = {
            "error_messages": [],
            "line_numbers": [],
            "function_names": [],
            "performance_metrics": {},
            "numerical_issues": []
        }
        
        # Extract error messages
        error_patterns = [
            r"error:\s*(.+)",
            r"Error:\s*(.+)", 
            r"ERROR:\s*(.+)",
            r"exception:\s*(.+)"
        ]
        
        for pattern in error_patterns:
            matches = re.findall(pattern, feedback_text, re.IGNORECASE)
            details["error_messages"].extend(matches)
        
        # Extract line numbers
        line_patterns = [
            r"line (\d+)",
            r":(\d+):",
            r"at line (\d+)"
        ]
        
        for pattern in line_patterns:
            matches = re.findall(pattern, feedback_text)
            details["line_numbers"].extend([int(match) for match in matches])
        
        # Extract performance metrics
        perf_patterns = {
            "latency": r"(\d+\.?\d*)\s*ms",
            "bandwidth": r"(\d+\.?\d*)\s*GB/s",
            "speedup": r"(\d+\.?\d*)x",
            "efficiency": r"(\d+\.?\d*)%"
        }
        
        for metric, pattern in perf_patterns.items():
            matches = re.findall(pattern, feedback_text)
            if matches:
                details["performance_metrics"][metric] = [float(m) for m in matches]
        
        # Extract numerical issues
        if "nan" in feedback_text.lower():
            details["numerical_issues"].append("NaN values detected")
        if "inf" in feedback_text.lower():
            details["numerical_issues"].append("Infinite values detected")
        if "overflow" in feedback_text.lower():
            details["numerical_issues"].append("Numerical overflow")
        
        return details

    def generate_targeted_fixes(self, issue_type: str, error_details: dict, kernel_code: str) -> list:
        """Generate specific fixes based on issue type and details"""
        fixes = []
        
        if issue_type == "compilation_error":
            # Common compilation fixes
            if any("undefined" in msg for msg in error_details["error_messages"]):
                fixes.append("Add missing headers: #include <hip/hip_runtime.h>, #include <iostream>, #include <cmath>")
                fixes.append("Check for typos in function names and variable declarations")
            
            if any("expected" in msg for msg in error_details["error_messages"]):
                fixes.append("Check for missing semicolons, brackets, or parentheses")
                fixes.append("Verify correct syntax for kernel launch configuration <<<grid, block>>>")
        
        elif issue_type == "runtime_error":
            # Runtime error fixes
            fixes.append("Add bounds checking: if (idx >= size) return; at the beginning of kernel")
            fixes.append("Validate all device pointers are non-null before kernel launch")
            fixes.append("Check grid and block dimensions are within hardware limits")
            fixes.append("Add hipDeviceSynchronize() and error checking after kernel launch")
        
        elif issue_type == "correctness_issue":
            # Correctness fixes
            if "nan" in error_details["numerical_issues"]:
                fixes.append("Add checks for division by zero and invalid mathematical operations")
                fixes.append("Use robust numerical algorithms that handle edge cases")
            
            fixes.append("Verify algorithm logic matches the expected mathematical operation")
            fixes.append("Check for race conditions in shared memory access")
            fixes.append("Ensure proper data type precision for intermediate calculations")
        
        elif issue_type == "performance_issue":
            # Performance optimization fixes
            fixes.append("Optimize memory access patterns for coalescing")
            fixes.append("Use shared memory to reduce global memory accesses")
            fixes.append("Tune block size for better occupancy")
            fixes.append("Consider vectorized loads/stores (float2, float4)")
            fixes.append("Add __launch_bounds__ directive for occupancy tuning")
        
        return fixes

    def generate_optimization_suggestions(self, kernel_code: str, feedback_text: str) -> list:
        """Generate performance optimization suggestions beyond basic fixes"""
        suggestions = []
        
        # Memory optimization suggestions
        if "global" in kernel_code.lower():
            suggestions.append("Consider using shared memory for frequently accessed data")
        
        if "__syncthreads" not in kernel_code:
            suggestions.append("Add __syncthreads() for proper thread synchronization in shared memory usage")
        
        # Compute optimization suggestions  
        if "for" in kernel_code and "unroll" not in kernel_code:
            suggestions.append("Consider loop unrolling with #pragma unroll for small, fixed-size loops")
        
        if "__launch_bounds__" not in kernel_code:
            suggestions.append("Add __launch_bounds__ directive to optimize register usage and occupancy")
        
        # Algorithm-specific suggestions
        if "sqrt" in kernel_code:
            suggestions.append("Consider using rsqrt() for reciprocal square root operations")
        
        if "pow" in kernel_code:
            suggestions.append("Replace pow() with multiplication for integer powers")
        
        return suggestions

    def analyse(self, kernel_code: str, feedback_text: str) -> str:
        """Main analysis function with comprehensive feedback processing"""
        
        # Step 1: Classify the issue
        issue_type = self.classify_issue_type(feedback_text)
        
        # Step 2: Extract detailed error information
        error_details = self.extract_error_details(feedback_text)
        
        # Step 3: Generate targeted fixes
        targeted_fixes = self.generate_targeted_fixes(issue_type, error_details, kernel_code)
        
        # Step 4: Generate optimization suggestions
        optimization_suggestions = self.generate_optimization_suggestions(kernel_code, feedback_text)
        
        # Step 5: Create comprehensive analysis prompt
        analysis_prompt = f"""
**Kernel Analysis Request:**

**Kernel Code:**
```cpp
{kernel_code}
```

**Execution Feedback:**
```
{feedback_text}
```

**Preliminary Analysis:**
- Issue Type: {issue_type}
- Error Details: {error_details}
- Suggested Fixes: {targeted_fixes}
- Optimization Opportunities: {optimization_suggestions}

**Required Analysis:**
Provide a comprehensive analysis addressing:

1. **Root Cause Identification**: What is the fundamental issue causing this problem?

2. **Immediate Action Items**: What specific changes need to be made to fix the issue?

3. **Code Location Analysis**: Which specific lines or sections of code need attention?

4. **Performance Impact Assessment**: How does this issue affect kernel performance?

5. **Optimization Strategy**: Beyond fixing the issue, what optimizations can be applied?

6. **Prevention Strategy**: How can similar issues be avoided in future iterations?

7. **Testing and Validation**: What steps should be taken to verify the fixes?

Format your response as a structured analysis that provides actionable guidance for kernel improvement.
"""
        
        return self.ask(analysis_prompt)

