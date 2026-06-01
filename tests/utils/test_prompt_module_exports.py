from src.services.research.graph.sales.prompts import (
    ALIGNMENT_PROMPT,
    CAMPAIGN_SIGNALS_PROMPT,
    FIRMOGRAPHICS_PROMPT,
    REPORT_COMPILER_PROMPT,
)
from src.services.research.graph.sales.prompts.company_prompts import (
    FIRMOGRAPHICS_PROMPT as COMPANY_FIRMOGRAPHICS_PROMPT,
)
from src.services.research.graph.sales.prompts.signal_prompts import (
    CAMPAIGN_SIGNALS_PROMPT as SIGNAL_CAMPAIGN_PROMPT,
)
from src.services.research.graph.sales.prompts.synthesis_alignment_prompts import (
    ALIGNMENT_PROMPT as MOD_ALIGNMENT_PROMPT,
)
from src.services.research.graph.sales.prompts.synthesis_report_prompts import (
    REPORT_COMPILER_PROMPT as MOD_REPORT_PROMPT,
)


def test_research_prompt_wrappers_reexport_company_and_signal_prompts() -> None:
    assert FIRMOGRAPHICS_PROMPT == COMPANY_FIRMOGRAPHICS_PROMPT
    assert CAMPAIGN_SIGNALS_PROMPT == SIGNAL_CAMPAIGN_PROMPT


def test_synthesis_prompt_wrappers_reexport_modular_prompts() -> None:
    assert ALIGNMENT_PROMPT == MOD_ALIGNMENT_PROMPT
    assert REPORT_COMPILER_PROMPT == MOD_REPORT_PROMPT
