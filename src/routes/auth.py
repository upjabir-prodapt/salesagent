"""Authentication API Routes - Endpoints for auth operations."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from ..core.config import settings
from ..core.security import create_access_token
from ..dependencies.service_dependencies import get_bigquery_repository
from ..models.auth_schemas import AuthRequest, Token
from ..repositories.bigquery_repository import BigQueryRepository

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)

BigQueryRepoDep = Annotated[BigQueryRepository, Depends(get_bigquery_repository)]

@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: AuthRequest,
    bq_repo: BigQueryRepoDep
):
    """Authenticate user and return a JWT access token"""
    logger.info(f"Authentication attempt for user: {request.email}")
    
    user = bq_repo.verify_user(
        email=request.email,
        business_unit=request.business_unit,
        organization=request.organization
    )
    
    if not user:
        logger.warning(f"Authentication failed for user: {request.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create token encoding the user's identity and details
    access_token = create_access_token(
        data={
            "sub": user["email"],
            "business_unit": user["business_unit"],
            "organization": user["organization"]
        }
    )
    
    logger.info(f"Authentication successful for user: {request.email}")
    return Token(access_token=access_token, token_type="bearer")
