/**
 * #FPORTALGRILLA01-12003: Selección de la página visible sin estado persistente.
 * La delegación de eventos sobre document sobrevive al reemplazo de la grilla
 * por HTMX. Cada consulta obtiene los controles actuales: no conserva nodos
 * eliminados ni registra nuevamente manejadores después de cada intercambio.
 * Los permisos y el ámbito de las operaciones se validan en el servidor.
 */
(() => {
  "use strict";

  const refresh = selectAll => {
    const root = document.getElementById("tabla-alumnos-container");
    if (!root) return;
    const all = root.querySelector("#student-select-all");
    const toolbar = root.querySelector("#student-bulk-toolbar");
    const counter = root.querySelector("#student-selection-count");
    if (!all || !toolbar || !counter) return;

    const boxes = [...root.querySelectorAll(".student-select")];
    if (selectAll) boxes.forEach(box => { box.checked = all.checked; });
    const selected = boxes.filter(box => box.checked).length;
    toolbar.hidden = selected === 0;
    counter.textContent = String(selected);
    all.checked = boxes.length > 0 && selected === boxes.length;
    all.indeterminate = selected > 0 && selected < boxes.length;
  };

  document.addEventListener("change", event => {
    if (event.target.matches("#student-select-all, .student-select")) {
      refresh(event.target.id === "student-select-all");
    }
  });
  // #FPORTALGRILLA02-12004: Incluye cargas iniciales, historial y respuestas HTMX.
  document.addEventListener("htmx:load", () => refresh(false));
  window.addEventListener("pageshow", () => refresh(false));
  refresh(false);
})();
