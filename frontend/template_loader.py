"""Small dependency-free loader for the project's HTML templates."""

from string import Template

from frontend import TEMPLATES_DIR

def render_template(name, **context):
    """Load a template file and substitute explicitly supplied values."""
    source = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    return Template(source).safe_substitute(context)
