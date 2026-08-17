"""Test end-to-end manuel du chat, piloté par un vrai navigateur (Playwright).

Contrairement à test_web_app.py (qui mocke ClaudeSDKClient et Qdrant), ce
script tape sur l'application réellement démarrée — il faut donc :
  - uvicorn web_app:app qui tourne sur http://127.0.0.1:8000
  - Qdrant accessible sur localhost:6333
  - une session Claude Code authentifiée

Pas nommé test_*.py exprès : pytest ne le ramasse pas automatiquement dans
`pytest -v` (ce serait un test qui casse dès que le serveur n'est pas lancé).

Usage :
    pip install playwright && playwright install chromium
    python3 tests/playwright_e2e.py
    python3 tests/playwright_e2e.py --headed   # pour voir le navigateur agir
"""

import sys

from playwright.sync_api import sync_playwright

APP_URL = "http://127.0.0.1:8000/"


def main():
    headed = "--headed" in sys.argv

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, slow_mo=300 if headed else 0)
        page = browser.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

        print(f"→ Ouverture de {APP_URL}")
        page.goto(APP_URL)
        page.wait_for_selector("h1")
        title = page.inner_text("h1")
        print(f"  Titre trouvé : {title!r}")

        print("→ Envoi d'un message de chat")
        page.locator("input").fill("Dis juste bonjour en un mot.")
        page.locator("button[type=submit]").click()

        print("→ Attente de la réponse assistant...")
        page.wait_for_selector(".msg.assistant", timeout=30000)
        page.wait_for_selector(".meta", timeout=30000)  # ligne coût/durée = fin du stream

        assistant_text = page.locator(".msg.assistant").first.inner_text()
        meta_text = page.locator(".meta").first.inner_text()
        print(f"  Réponse assistant : {assistant_text!r}")
        print(f"  Meta (coût/durée) : {meta_text!r}")

        browser.close()

        print()
        if console_errors:
            print("❌ Erreurs console JS détectées :")
            for err in console_errors:
                print("   ", err)
            sys.exit(1)

        print("✅ Aucune erreur console JS.")

        if not assistant_text.strip():
            print("❌ Réponse assistant vide.")
            sys.exit(1)
        if "Coût" not in meta_text:
            print("❌ Ligne coût/durée absente ou mal formée.")
            sys.exit(1)

        print("✅ Test end-to-end réussi.")


if __name__ == "__main__":
    main()
