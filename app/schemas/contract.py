from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2)
    unified_social_credit_code: str = ""
    address: str = ""
    legal_representative: str = ""
    contact_name: str = ""
    contact_phone: str = ""


class ContractGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    template_type: Literal["信息化服务合同"] = "信息化服务合同"
    contract_number: str = ""
    purchaser: ContractParty
    supplier: ContractParty
    contract_amount: Decimal = Field(gt=0)
    service_start_date: date
    service_end_date: date
    service_scope: list[str] = Field(min_length=1)
    payment_terms: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    breach_terms: list[str] = Field(default_factory=list)
    dispute_resolution: str = "协商不成的，向采购人所在地有管辖权的人民法院提起诉讼。"
    source_task_id: str = ""
    use_dify: bool = True


class ContractValidationItem(BaseModel):
    code: str
    level: Literal["error", "warning", "info"]
    message: str
    requires_human_review: bool = False


class ContractGenerationResult(BaseModel):
    contract_id: str
    contract_number: str
    template_type: str
    status: Literal["generated", "review_required", "failed"]
    validation_items: list[ContractValidationItem] = Field(default_factory=list)
    requires_human_review: bool = True
    document_url: str = ""
    pdf_url: str = ""
    execution_mode: Literal["local", "dify", "local_fallback"] = "local"
    dify_errors: list[str] = Field(default_factory=list)
