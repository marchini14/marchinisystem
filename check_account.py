"""Provjera API kljuca i stanja racuna — pokrenite OVO PRVO.

    python check_account.py

Ispisuje dozvole kljuca i stanje novcanika. Ako kljuc ima dozvolu za
povlacenje sredstava (Withdrawal), skripta glasno upozorava.
"""

import os
import sys

from dotenv import load_dotenv
from pybit.unified_trading import HTTP


def main() -> None:
    load_dotenv()
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    env = os.getenv("BYBIT_ENV", "demo").lower()

    if not api_key or not api_secret or "UPISITE" in api_key:
        sys.exit("GRESKA: upisite BYBIT_API_KEY i BYBIT_API_SECRET u .env datoteku.")

    session = HTTP(
        testnet=(env == "testnet"),
        demo=(env == "demo"),
        api_key=api_key,
        api_secret=api_secret,
    )

    print(f"Okruzenje: {env}")

    info = session.get_api_key_information()["result"]
    print(f"\nAPI kljuc:      {info.get('apiKey')}")
    print(f"Read-only:      {'DA — bot NECE moci trgovati!' if info.get('readOnly') else 'ne (moze trgovati)'}")
    print(f"Istice:         {info.get('expiredAt') or 'nema roka'}")
    perms = info.get("permissions", {})
    for group, items in perms.items():
        if items:
            print(f"Dozvola {group}: {', '.join(items)}")
    if perms.get("Wallet") and any("Withdraw" in p for p in perms["Wallet"]):
        print("\n!!! UPOZORENJE: kljuc ima dozvolu za POVLACENJE sredstava. "
              "Obrisite ga i napravite novi samo s Trade dozvolom !!!")

    balance = session.get_wallet_balance(accountType="UNIFIED")["result"]["list"]
    print("\nStanje novcanika (UNIFIED):")
    for account in balance:
        for coin in account.get("coin", []):
            amount = float(coin.get("walletBalance") or 0)
            if amount > 0:
                print(f"  {coin['coin']}: {amount}")

    print("\nSve OK — mozete pokrenuti bota: python bot.py")


if __name__ == "__main__":
    main()
