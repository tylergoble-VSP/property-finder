"""The only door to the internet.

Every network call this tool makes goes through this package, so there is exactly one
place where a provider's vocabulary is translated into the tool's own. Tests replace the
door with a fake transport and never open the real one.
"""
from propertyfinder.adapters.zillow import (
    BASE_URL,
    PropertyDetail,
    SchemaDrift,
    ZillowAdapter,
    ZillowHTTPError,
)

__all__ = [
    "BASE_URL",
    "PropertyDetail",
    "SchemaDrift",
    "ZillowAdapter",
    "ZillowHTTPError",
]
