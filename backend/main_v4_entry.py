from main import app
from v4 import router as v4_router

app.include_router(v4_router)
