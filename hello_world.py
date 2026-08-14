"""Simple Hello World script.

Usage:
    python3 hello_world.py [name]
"""

def main(name=None):
    if not name:
        name = "world"
    print(f"Hello, {name}!")


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)
