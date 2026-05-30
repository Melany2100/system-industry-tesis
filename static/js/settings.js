document.addEventListener("DOMContentLoaded", () => {
  const menuItems = document.querySelectorAll(".settings-menu-item");
  const sections = document.querySelectorAll(".settings-section");
  const darkModeToggle = document.getElementById("darkModeToggle");

  menuItems.forEach((item) => {
    item.addEventListener("click", () => {
      const sectionId = item.dataset.section;

      menuItems.forEach((btn) => btn.classList.remove("active"));
      item.classList.add("active");

      sections.forEach((section) => {
        section.classList.remove("active");
      });

      const selectedSection = document.getElementById(sectionId);

      if (selectedSection) {
        selectedSection.classList.add("active");
      }
    });
  });

  if (localStorage.getItem("darkMode") === "enabled") {
    document.body.classList.add("dark-settings");

    if (darkModeToggle) {
      darkModeToggle.checked = true;
    }
  }

  if (darkModeToggle) {
    darkModeToggle.addEventListener("change", () => {
      if (darkModeToggle.checked) {
        document.body.classList.add("dark-settings");
        localStorage.setItem("darkMode", "enabled");
      } else {
        document.body.classList.remove("dark-settings");
        localStorage.setItem("darkMode", "disabled");
      }
    });
  }
});