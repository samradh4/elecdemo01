from main import app
from v4 import router as v4_router
from v41 import router as v41_router

app.include_router(v4_router)
app.include_router(v41_router)
