"""CLI entry point for odoo-instance-sdk."""

from odoo_instance_sdk import __version__


def main() -> None:
    print(f"odoo-instance-sdk v{__version__}")


if __name__ == "__main__":
    main()
