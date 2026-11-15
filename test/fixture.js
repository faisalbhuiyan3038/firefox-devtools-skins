// Fixture script: populates the console and network panels.
"use strict";

const user = { id: 42, name: "Ada", roles: ["admin", "dev"], meta: { since: 2019, active: true } };

function greet(person, greeting = "Hello") {
  const message = `${greeting}, ${person.name}!`;
  console.log(message, person);
  return message;
}

class Store {
  #items = new Map();
  add(key, value) {
    this.#items.set(key, value);
    return this;
  }
  get size() {
    return this.#items.size;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  greet(user);
  console.info("Store size:", new Store().add("a", 1).add("b", 2).size);
  console.warn("Deprecated API used: document.write()");
  console.error(new TypeError("Cannot read properties of undefined (reading 'map')"));
  console.debug("debug detail", { nested: { deep: [1, 2, 3] } });
  console.table([{ lang: "JS", year: 1995 }, { lang: "Rust", year: 2015 }]);
  console.log("%cStyled log", "color: #7c5cff; font-weight: bold");
  for (const f of ["fixture.html", "fixture.js", "missing.json", "fixture.html?v=2"]) {
    fetch(f).catch(() => {});
  }
});
