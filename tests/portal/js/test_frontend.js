/**
 * #FPORTALPRUEBAS01-12301: Pruebas de comportamiento de los módulos sin bibliotecas.
 * Los dobles representan el DOM mínimo y permiten reemplazar una grilla como
 * lo hace HTMX. Se comprueban eventos y efectos visibles, sin usar servicios.
 */
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const { test } = require("node:test");
const directory = resolve(__dirname, "../../../apps/portal/static/portal/js");
const run = (file, context) => runInNewContext(readFileSync(resolve(directory, file), "utf8"), context);

function environment() {
  const handlers = {};
  const nodes = {};
  return {
    handlers, nodes,
    document: {
      getElementById: id => nodes[id],
      addEventListener: (name, callback) => { handlers[name] = callback; },
    },
    window: { addEventListener: (name, callback) => { handlers[name] = callback; } },
  };
}

function grid(env, count) {
  const boxes = Array.from({ length: count }, () => ({ checked: false }));
  const all = { id: "student-select-all", checked: false };
  const toolbar = {};
  const counter = {};
  env.nodes["tabla-alumnos-container"] = {
    querySelector: selector => ({ "#student-select-all": all,
      "#student-bulk-toolbar": toolbar, "#student-selection-count": counter })[selector],
    querySelectorAll: () => boxes,
  };
  return { boxes, all, toolbar, counter };
}

const change = (env, target) => env.handlers.change({ target: { ...target, matches: () => true } });

test("selección total, parcial y vacía actualiza contador, barra y estado intermedio", () => {
  const env = environment();
  const current = grid(env, 2);
  run("student_grid.js", env);
  assert.equal(current.toolbar.hidden, true);
  current.all.checked = true;
  change(env, current.all);
  assert.equal(current.counter.textContent, "2");
  assert.equal(current.toolbar.hidden, false);
  current.boxes[0].checked = false;
  change(env, current.boxes[0]);
  assert.equal(current.counter.textContent, "1");
  assert.equal(current.all.indeterminate, true);
  current.all.checked = false;
  change(env, current.all);
  assert.equal(current.toolbar.hidden, true);
  assert.equal(current.all.indeterminate, false);
});

test("el intercambio HTMX usa nodos nuevos sin operar sobre la selección anterior", () => {
  const env = environment();
  const old = grid(env, 1);
  run("student_grid.js", env);
  const current = grid(env, 3);
  env.handlers["htmx:load"]();
  current.all.checked = true;
  change(env, current.all);
  assert.equal(current.counter.textContent, "3");
  assert.equal(old.boxes[0].checked, false);
  assert.equal(Object.keys(env.handlers).length, 3);
});

test("grillas sin resultados y páginas sin controles operativos no producen errores", () => {
  const env = environment();
  const current = grid(env, 0);
  run("student_grid.js", env);
  assert.equal(current.all.checked, false);
  delete env.nodes["tabla-alumnos-container"];
  env.handlers["htmx:load"]();
  env.nodes["tabla-alumnos-container"] = { querySelector: () => null };
  env.handlers.pageshow();
});

test("exportación incluye filtros actuales después de reemplazar el formulario", () => {
  const env = environment();
  const link = { dataset: { url: "/dash/postgrado/exportar/" } };
  env.nodes["student-filters"] = [["q", "Pérez"], ["page_size", "100"]];
  run("student_export.js", { ...env, URLSearchParams, FormData: function (form) { return form; } });
  const click = () => env.handlers.click({ target: { closest: () => link } });
  click();
  assert.equal(new URL("https://ejemplo.test" + link.href).searchParams.get("q"), "Pérez");
  env.nodes["student-filters"] = [["formula", "saga:PRE AND licencia:NO"]];
  click();
  const params = new URL("https://ejemplo.test" + link.href).searchParams;
  assert.equal(params.get("formula"), "saga:PRE AND licencia:NO");
  assert.equal(params.has("q"), false);
});

test("menú móvil sincroniza accesibilidad, Escape y cambios de ancho", () => {
  const env = environment();
  const attrs = {};
  const buttonEvents = {};
  const navigationEvents = {};
  const desktop = { matches: false, addEventListener: (_, callback) => { env.resize = callback; } };
  env.window.matchMedia = () => desktop;
  env.nodes["admin-menu-toggle"] = {
    setAttribute: (key, value) => { attrs[key] = value; },
    addEventListener: (key, callback) => { buttonEvents[key] = callback; },
    focus: () => { env.focused = true; },
  };
  const navigation = env.nodes["admin-navigation"] = {
    addEventListener: (key, callback) => { navigationEvents[key] = callback; },
  };
  run("admin_navigation.js", env);
  assert.equal(attrs["aria-expanded"], "false");
  buttonEvents.click();
  assert.equal(navigation.hidden, false);
  navigationEvents.keydown({ key: "Escape" });
  assert.equal(navigation.hidden, true);
  assert.equal(env.focused, true);
  desktop.matches = true;
  env.resize();
  assert.equal(attrs["aria-expanded"], "true");
  run("admin_navigation.js", environment());
});
