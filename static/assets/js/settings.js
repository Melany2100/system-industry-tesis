document.addEventListener('DOMContentLoaded', function () {
  const page = document.querySelector('.settings-page');

  if (!page) {
    return;
  }

  const menuItems = page.querySelectorAll('.settings-menu-item[data-section]');
  const sections = page.querySelectorAll('.settings-section');
  const saveButton = page.querySelector('.settings-save-btn');
  const feedback = page.querySelector('.settings-feedback');
  const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');

  const urls = {
    profile: page.dataset.updateProfileUrl,
    email: page.dataset.updateEmailUrl,
    password: page.dataset.updatePasswordUrl,
    appearance: page.dataset.updateAppearanceUrl,
    darkmode: page.dataset.updateAppearanceUrl,
    notifications: page.dataset.updateNotificationsUrl,
    login: page.dataset.updateSecurityUrl,
  };

  function getActiveSectionId() {
    const activeSection = page.querySelector('.settings-section.active');
    return activeSection ? activeSection.id : 'profile';
  }

  function showFeedback(message, type) {
    feedback.textContent = message;
    feedback.className = `settings-feedback ${type || ''}`.trim();
  }

  function getValue(name) {
    const input = page.querySelector(`[name="${name}"]`);
    return input ? input.value.trim() : '';
  }

  function getChecked(name) {
    const input = page.querySelector(`[name="${name}"]`);
    return Boolean(input && input.checked);
  }

  function updateThemePreview() {
    const color = getValue('theme_color') || 'azul';
    const themeClasses = ['theme-verde', 'theme-azul', 'theme-morado', 'theme-rojo'];

    page.classList.remove(...themeClasses);
    page.classList.add(`theme-${color}`);
    page.classList.toggle('is-dark-mode', getChecked('dark_mode'));
    page.classList.toggle('is-compact-layout', getChecked('compact_layout'));
  }

  function buildPayload(sectionId) {
    if (sectionId === 'profile') {
      return {
        username: getValue('username'),
        first_name: getValue('first_name'),
        last_name: getValue('last_name'),
      };
    }

    if (sectionId === 'email') {
      return {
        new_email: getValue('new_email'),
      };
    }

    if (sectionId === 'password') {
      return {
        current_password: getValue('current_password'),
        new_password: getValue('new_password'),
        confirm_password: getValue('confirm_password'),
      };
    }

    if (sectionId === 'appearance' || sectionId === 'darkmode') {
      return {
        theme_color: getValue('theme_color'),
        compact_layout: getChecked('compact_layout'),
        dark_mode: getChecked('dark_mode'),
      };
    }

    if (sectionId === 'notifications') {
      return {
        security_alerts: getChecked('security_alerts'),
        email_alerts: getChecked('email_alerts'),
      };
    }

    if (sectionId === 'login') {
      return {
        extra_verification: getChecked('extra_verification'),
      };
    }

    return null;
  }

  async function saveActiveSection() {
    const sectionId = getActiveSectionId();
    const url = urls[sectionId];
    const payload = buildPayload(sectionId);

    if (!url || !payload) {
      showFeedback('Esta seccion no tiene cambios para guardar.', 'info');
      return;
    }

    saveButton.disabled = true;
    saveButton.textContent = 'Guardando...';
    showFeedback('', '');

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfInput ? csrfInput.value : '',
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || 'No se pudo guardar la configuracion.');
      }

      showFeedback(data.message, 'success');
      updateThemePreview();

      if (sectionId === 'password') {
        ['current_password', 'new_password', 'confirm_password'].forEach(function (name) {
          const input = page.querySelector(`[name="${name}"]`);
          if (input) {
            input.value = '';
          }
        });
      }
    } catch (error) {
      showFeedback(error.message, 'error');
    } finally {
      saveButton.disabled = false;
      saveButton.textContent = 'Guardar cambios';
    }
  }

  menuItems.forEach(function (item) {
    item.addEventListener('click', function () {
      const sectionId = item.getAttribute('data-section');
      const targetSection = document.getElementById(sectionId);

      if (!targetSection) {
        showFeedback('No existe la seccion seleccionada.', 'error');
        return;
      }

      menuItems.forEach(function (btn) {
        btn.classList.remove('active');
      });

      sections.forEach(function (section) {
        section.classList.remove('active');
      });

      item.classList.add('active');
      targetSection.classList.add('active');
      showFeedback('', '');
    });
  });

  ['theme_color', 'compact_layout', 'dark_mode'].forEach(function (name) {
    const input = page.querySelector(`[name="${name}"]`);
    if (input) {
      input.addEventListener('change', updateThemePreview);
    }
  });

  saveButton.addEventListener('click', saveActiveSection);
  updateThemePreview();
});
