/**
 * #FPORTALEXCEL01-12005: Exportación con los filtros visibles en el formulario.
 * La delegación también reconoce formularios reemplazados por HTMX fuera de
 * banda. El servidor vuelve a validar filtros, permiso de lectura y ámbito.
 */
(() => {
  "use strict";
  document.addEventListener("click", event => {
    const link = event.target.closest("a[data-student-export]");
    const filters = document.getElementById("student-filters");
    if (!link || !filters) return;
    link.href = link.dataset.url + "?" + new URLSearchParams(new FormData(filters)).toString();
  });
})();
