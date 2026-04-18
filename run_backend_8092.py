import sys

sys.path[:0] = [
    r"C:\Users\shreeram\OneDrive\Desktop\Blueprint-nav\.deps_local",
    r"C:\Users\shreeram\OneDrive\Desktop\Blueprint-nav",
]

import uvicorn


if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8092)
