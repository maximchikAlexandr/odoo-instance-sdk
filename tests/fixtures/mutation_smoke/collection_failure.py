from odoo_instance_sdk import cli

assert callable(cli.cli)
raise RuntimeError("intentional mutation smoke collection failure")
