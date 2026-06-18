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
    'admin-users': page.dataset.createUserUrl,
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

    if (sectionId === 'admin-users') {
      return {
        username: getValue('new_user_username'),
        first_name: getValue('new_user_first_name'),
        last_name: getValue('new_user_last_name'),
        email: getValue('new_user_email'),
        password: getValue('new_user_password'),
        role: getValue('new_user_role') || 'operador',
      };
    }

    return null;
  }

  function clearInputs(names) {
    names.forEach(function (name) {
      const input = page.querySelector(`[name="${name}"]`);

      if (input) {
        input.value = '';
      }
    });
  }

  function prependUser(user) {
    const list = page.querySelector('#managedUsersList');

    if (!list) {
      return;
    }

    const row = document.createElement('div');
    const info = document.createElement('div');
    const name = document.createElement('strong');
    const meta = document.createElement('span');
    const badge = document.createElement('span');
    const fullName = user.full_name || user.username;
    const emailText = user.email ? ` | ${user.email}` : '';

    row.className = 'settings-user-row';
    badge.className = 'settings-role-badge';
    name.textContent = fullName;
    meta.textContent = `${user.username}${emailText}`;
    badge.textContent = user.role;
    info.append(name, meta);
    row.append(info, badge);
    list.prepend(row);
  }

  async function saveSection(sectionId) {
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

      if (sectionId === 'admin-users' && data.user) {
        prependUser(data.user);
        clearInputs([
          'new_user_username',
          'new_user_first_name',
          'new_user_last_name',
          'new_user_email',
          'new_user_password',
        ]);
      }

      if (sectionId === 'password') {
        clearInputs(['current_password', 'new_password', 'confirm_password']);
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

  page.querySelectorAll('[data-submit-section]').forEach(function (button) {
    button.addEventListener('click', function () {
      saveSection(button.getAttribute('data-submit-section'));
    });
  });

  saveButton.addEventListener('click', function () {
    saveSection(getActiveSectionId());
  });
});
