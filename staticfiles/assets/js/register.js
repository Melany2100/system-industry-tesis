document.addEventListener("DOMContentLoaded", function () {
  const usernameInput = document.getElementById("id_username");
  const emailInput = document.getElementById("id_email");
  const password1Input = document.getElementById("id_password1");
  const password2Input = document.getElementById("id_password2");
  const termsCheckbox = document.getElementById("customCheckRegister");
  const submitBtn = document.getElementById("submitBtn");

  const ruleLength = document.getElementById("ruleLength");
  const ruleLetter = document.getElementById("ruleLetter");
  const ruleNumber = document.getElementById("ruleNumber");

  // Aplicar placeholders y clases a los inputs renderizados por Django
  if (usernameInput) {
    usernameInput.placeholder = "Ejemplo: melany.vargas";
    usernameInput.classList.add("form-control-custom");
  }

  if (emailInput) {
    emailInput.placeholder = "Ejemplo: correo@empresa.com";
    emailInput.classList.add("form-control-custom");
  }

  if (password1Input) {
    password1Input.placeholder = "Crea una contraseña segura";
    password1Input.classList.add("form-control-custom");
  }

  if (password2Input) {
    password2Input.placeholder = "Repite tu contraseña";
    password2Input.classList.add("form-control-custom");
  }

  // Mostrar / ocultar contraseña
  const toggleButtons = document.querySelectorAll(".toggle-password");

  toggleButtons.forEach((button) => {
    button.addEventListener("click", function () {
      const targetId = this.getAttribute("data-target");
      const input = document.getElementById(targetId);

      if (!input) return;

      if (input.type === "password") {
        input.type = "text";
        this.innerHTML = '<i class="ni ni-eye"></i>';
      } else {
        input.type = "password";
        this.innerHTML = '<i class="ni ni-fat-remove"></i>';
      }
    });
  });

  // Validación visual de contraseña
  function validatePasswordRules() {
    if (!password1Input) return;

    const value = password1Input.value;
    const hasLength = value.length >= 8;
    const hasLetter = /[A-Za-z]/.test(value);
    const hasNumber = /\d/.test(value);

    ruleLength.classList.toggle("rule-valid", hasLength);
    ruleLetter.classList.toggle("rule-valid", hasLetter);
    ruleNumber.classList.toggle("rule-valid", hasNumber);
  }

  if (password1Input) {
    password1Input.addEventListener("input", validatePasswordRules);
  }

  // Habilitar botón solo si acepta términos
  function updateSubmitState() {
    if (!submitBtn || !termsCheckbox) return;
    submitBtn.disabled = !termsCheckbox.checked;
  }

  if (termsCheckbox) {
    termsCheckbox.addEventListener("change", updateSubmitState);
    updateSubmitState();
  }
});