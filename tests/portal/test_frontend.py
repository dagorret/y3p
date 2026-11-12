"""Recursos del portal y prevención de lógica de navegador embebida en plantillas."""

import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class ScriptCollector(HTMLParser):
    """Releva las referencias estáticas sin depender de la indentación del HTML."""

    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts.append(dict(attrs))


class FrontendAssetsTests(SimpleTestCase):
    """Protege la carga única de módulos y la separación de comportamiento visual."""

    def test_nested_portal_pages_receive_absolute_resolvable_deferred_scripts(self):
        parser = ScriptCollector()
        parser.feed(render_to_string('portal/base.html'))
        local = [script for script in parser.scripts if '/portal/js/' in script.get('src', '')]
        self.assertEqual(len(local), 3)
        self.assertEqual(len({script['src'] for script in local}), 3)
        for script in local:
            with self.subTest(recurso=script['src']):
                #FPORTALPRUEBAS03-12303: Las rutas anidadas no deben resolver recursos relativos.
                self.assertTrue(script['src'].startswith('/static/portal/js/'))
                self.assertIn('defer', script)
                self.assertIsNotNone(finders.find(script['src'].removeprefix('/static/')))

    def test_owned_templates_do_not_embed_scripts_or_event_handlers(self):
        roots = (settings.BASE_DIR / 'apps/portal/templates', settings.BASE_DIR / 'templates')
        for root in roots:
            for path in Path(root).rglob('*.html'):
                with self.subTest(plantilla=str(path.relative_to(settings.BASE_DIR))):
                    source = path.read_text()
                    #FPORTALPRUEBAS04-12304: El HTML declara anclajes; los módulos contienen los eventos.
                    self.assertIsNone(re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', source, re.I))
                    self.assertIsNone(re.search(r'\bon(?:click|change|submit|load|keyup)\s*=', source, re.I))
