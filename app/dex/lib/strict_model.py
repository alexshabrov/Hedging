from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', arbitrary_types_allowed=True, strict=True)