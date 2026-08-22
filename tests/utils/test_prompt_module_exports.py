"""The per-domain company_prompts/signal_prompts modules were removed when the
12 research leaf agents were consolidated into the unified QueryGeneratorAgent,
so only the synthesis prompt wrappers remain to verify.
"""

from src.services.research.agents.sales.prompts import (
    ALIGNMENT_PROMPT,
    REPORT_COMPILER_PROMPT,
    RESEARCH_SYNTHESIZER_PROMPT,
)
from src.services.research.agents.sales.prompts.synthesis_alignment_prompts import (
    ALIGNMENT_PROMPT as MOD_ALIGNMENT_PROMPT,
)
from src.services.research.agents.sales.prompts.synthesis_report_prompts import (
    REPORT_COMPILER_PROMPT as MOD_REPORT_PROMPT,
)
from src.services.research.agents.sales.prompts.synthesis_research_prompts import (
    RESEARCH_SYNTHESIZER_PROMPT as MOD_SYNTHESIZER_PROMPT,
)


def test_synthesis_prompt_wrappers_reexport_modular_prompts() -> None:
    assert ALIGNMENT_PROMPT == MOD_ALIGNMENT_PROMPT
    assert REPORT_COMPILER_PROMPT == MOD_REPORT_PROMPT


def test_research_synthesizer_prompt_is_reexported() -> None:
    assert RESEARCH_SYNTHESIZER_PROMPT == MOD_SYNTHESIZER_PROMPT
    assert RESEARCH_SYNTHESIZER_PROMPT.strip()
