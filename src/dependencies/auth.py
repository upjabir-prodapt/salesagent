"""Authentication Dependencies for FastAPI Routes"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from loguru import logger

from ..core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/token")

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    """Dependency to validate JWT token and return current user details"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        
        # User details were encoded in the token during creation
        user_data = {
            "email": email,
            "business_unit": payload.get("business_unit"),
            "organization": payload.get("organization")
        }
        return user_data
    except jwt.PyJWTError as e:
        logger.error(f"JWT validation error: {e}")
        raise credentials_exception
