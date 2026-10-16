from app.core.config import Settings
from app.providers.base import MarketDataProvider
from app.providers.groww import GrowwProvider
from app.providers.sample import SampleProvider


def get_provider(settings: Settings) -> MarketDataProvider:
    """Pick the data source from DATA_PROVIDER in .env ("sample" or "groww")."""
    name = settings.data_provider.strip().lower()
    if name == "sample":
        return SampleProvider()
    if name == "groww":
        return GrowwProvider(settings)
    raise ValueError(f"Unknown DATA_PROVIDER '{settings.data_provider}'. Use 'sample' or 'groww'.")
