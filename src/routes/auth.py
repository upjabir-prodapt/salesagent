"""Authentication API Routes - Endpoints for auth operations."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from ..core.config import settings
from ..core.security import create_access_token
from ..models.auth_schemas import AuthRequest, Token

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)

@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: AuthRequest
):
    """Authenticate user and return a JWT access token"""
    logger.info(f"Authentication attempt for user: {request.email}")
    
    # Validate email suffix
    if not request.email.lower().endswith("@colt.net"):
        logger.warning(f"Authentication failed (invalid domain) for user: {request.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email domain. Only @colt.net is allowed.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create token encoding the user's identity and details
    access_token = create_access_token(
        data={
            "sub": request.email,
            "business_unit": request.business_unit,
            "organization": request.organization
        }
    )
    
    logger.info(f"Authentication successful for user: {request.email}")
    return Token(access_token=access_token, token_type="bearer")
