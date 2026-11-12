/**
 * #FPORTALNAVEGACION01-12002: Control accesible del menú lateral adaptable.
 * El servidor decide los enlaces visibles por ámbito y permiso. Este módulo
 * únicamente abre y cierra la navegación, sin alterar autorizaciones.
 */
(() => {
  "use strict";
  const button = document.getElementById("admin-menu-toggle");
  const navigation = document.getElementById("admin-navigation");
  if (!button || !navigation) return;

  const desktop = window.matchMedia("(min-width: 1024px)");
  const update = expanded => {
    navigation.hidden = !expanded;
    button.setAttribute("aria-expanded", String(expanded));
  };

  button.addEventListener("click", () => update(navigation.hidden));
  navigation.addEventListener("keydown", event => {
    if (event.key === "Escape" && !desktop.matches) {
      update(false);
      button.focus();
    }
  });
  desktop.addEventListener("change", () => update(desktop.matches));
  update(desktop.matches);
})();
