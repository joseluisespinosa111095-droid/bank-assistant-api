from typing import Optional

from sqlalchemy.orm import Session

from models import CustomerModel
from services.serializers_core import serialize_customer


def get_customer_detail_payload(db: Session, tenant_id: int, customer_id: int) -> Optional[dict]:
    """Returns the strictly Core customer profile payload (0 Lending dependencies).

    Returns None when the customer does not exist in `tenant_id` so the HTTP layer
    can raise the appropriate 404 response without coupling services/ to FastAPI.
    """
    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == tenant_id)
        .first()
    )
    if not customer:
        return None

    return serialize_customer(customer)
