"""NoticeMe — thin entry point for `python main.py` (development convenience)."""

from .app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("NoticeMe.main:app", host="0.0.0.0", port=8200, reload=True)
