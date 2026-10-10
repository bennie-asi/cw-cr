"""Initialize the configured project and empty global configuration rows."""

from review.management.commands.bootstrap_data import Command as BootstrapCommand


class Command(BootstrapCommand):
    help = 'Initialize the configured GitLab project and empty platform configuration.'
