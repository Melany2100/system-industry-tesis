$(document).ready(function () {
  // Inicializar tooltips de Bootstrap
  const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl);
  });

  let lastVideoErrorAt = 0;
  let cameraStatusTimer = null;

  const $page = $('.security-page');

  const APP_CONFIG = {
    urls: {
      events: $page.data('events-url'),
      registerFace: $page.data('register-face-url')
    },
    streamMode: String($page.data('stream-mode') || 'mjpeg'),
    targetVideoFps: Number($page.data('target-video-fps')) || 8,
    eventsRefreshMs: Number($page.data('events-refresh-ms')) || 5000,
    liveLogRefreshMs: Number($page.data('live-log-refresh-ms')) || 800,
    cameraStatusRefreshMs: Number($page.data('camera-status-refresh-ms')) || 3000
  };

  function escapeHtml(str) {
    return String(str || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function updateTimestamp() {
    const now = new Date();

    const fullDateTime = now.toLocaleString('es-EC', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });

    $('#lastUpdate').text('Última actualización: ' + fullDateTime);
    $('#cameraDateTime').text('Fecha y hora actual: ' + fullDateTime);
  }

  function setCameraStatus(status) {
    const $badge = $('#cameraStatusBadge');

    if (!$badge.length) return;

    const recentVideoError = lastVideoErrorAt && ((Date.now() - lastVideoErrorAt) < 6000);

    if (status && status.status === 'active' && recentVideoError) {
      status = {
        status: 'no_signal',
        message: 'El navegador no pudo cargar el video de la cámara.'
      };
    }

    $badge.removeClass(
      'camera-status--checking camera-status--active camera-status--inactive camera-status--no-signal'
    );

    let label = 'Verificando cámara...';
    let className = 'camera-status--checking';

    if (status && status.status === 'active') {
      label = 'Activa';
      className = 'camera-status--active';
    } else if (status && status.status === 'inactive') {
      label = 'Inactiva';
      className = 'camera-status--inactive';
    } else if (status && status.status === 'no_signal') {
      label = 'Sin señal';
      className = 'camera-status--no-signal';
    }

    $badge.addClass(className);
    $badge.find('strong').text(label);

    if (status && status.message) {
      $badge.attr('title', status.message);
    }
  }

  function getSelectedCameraStatusUrl() {
    const $selectedOption = $('#cameraSelector').find(':selected');

    if (!$selectedOption.length) return null;

    return $selectedOption.data('status-url') || null;
  }

  function loadCameraStatus() {
    // En cloud el video llega directamente desde MediaMTX. El worker de Django
    // no abre la cámara y, por tanto, no debe usarse para determinar su estado.
    if (APP_CONFIG.streamMode === 'mediamtx') return;

    const statusUrl = getSelectedCameraStatusUrl();

    if (!statusUrl) {
      setCameraStatus({
        status: 'inactive',
        message: 'No hay cámara seleccionada.'
      });
      return;
    }

    $.ajax({
      url: statusUrl,
      method: 'GET',
      success: function (data) {
        setCameraStatus(data);
      },
      error: function (xhr) {
        console.log('❌ camera_status error:', xhr.status, xhr.responseText);

        setCameraStatus({
          status: 'no_signal',
          message: 'No se pudo consultar el estado de la cámara.'
        });
      }
    });
  }

  function ensureVideoFeedExists(streamKind) {
    const expectedTag = streamKind === 'mediamtx' ? 'IFRAME' : 'IMG';
    const currentFeed = document.getElementById('videoFeed');

    if (currentFeed && currentFeed.tagName === expectedTag) return;
    if (currentFeed) currentFeed.remove();

    if (streamKind === 'mediamtx') {
      $('#cameraScreen').prepend(`
        <iframe
          class="camera-feed"
          id="videoFeed"
          title="Video en vivo"
          allow="autoplay; fullscreen; picture-in-picture"
          referrerpolicy="same-origin"
          allowfullscreen></iframe>
      `);
      return;
    }

    $('#cameraScreen').prepend(`
      <img class="camera-feed" id="videoFeed" alt="Video en vivo">
    `);
  }

  function showNoCameraBox(message) {
    $('#videoFeed').remove();

    if (!$('#noCameraBox').length) {
      $('#cameraScreen').prepend(`
        <div class="no-camera-box" id="noCameraBox">
          <i class="fas fa-video-slash"></i>
          <span>${escapeHtml(message)}</span>
        </div>
      `);
    } else {
      $('#noCameraBox span').text(message);
      $('#noCameraBox').show();
    }
  }

  function changeCamera() {
    const $selectedOption = $('#cameraSelector').find(':selected');

    if (!$selectedOption.length) {
      showNoCameraBox('No hay cámara seleccionada.');
      setCameraStatus({
        status: 'inactive',
        message: 'No hay cámara seleccionada.'
      });
      return;
    }

    const videoUrl = $selectedOption.val();
    const streamKind = String($selectedOption.data('stream-kind') || APP_CONFIG.streamMode);
    const label = $selectedOption.data('label');
    const isActive = String($selectedOption.data('active')) === 'true';

    $('#cameraLabel').text(label || 'Cámara sin nombre');

    setCameraStatus({
      status: 'checking',
      message: 'Verificando señal de la cámara...'
    });

    if (!isActive) {
      showNoCameraBox('La cámara seleccionada está inactiva.');

      setCameraStatus({
        status: 'inactive',
        message: 'La cámara está desactivada en el sistema.'
      });

      return;
    }

    if (!videoUrl || streamKind === 'unconfigured') {
      showNoCameraBox('Configura MEDIAMTX_PUBLIC_URL en el servidor.');
      setCameraStatus({
        status: 'no_signal',
        message: 'Falta configurar la URL pública de MediaMTX.'
      });
      return;
    }

    $('#noCameraBox').remove();
    ensureVideoFeedExists(streamKind);

    const finalUrl = streamKind === 'mediamtx'
      ? videoUrl
      : videoUrl + '?fps=' + APP_CONFIG.targetVideoFps + '&t=' + Date.now();

    lastVideoErrorAt = 0;

    $('#videoFeed')
    .off('load')
    .off('error')
    .on('load', function () {
      if (streamKind === 'mediamtx') {
        setCameraStatus({
          status: 'active',
          message: 'Reproductor WebRTC conectado a MediaMTX.'
        });
      }
    })
    .on('error', function () {
        lastVideoErrorAt = Date.now();

        setCameraStatus({
        status: 'no_signal',
        message: 'No se pudo cargar la señal de video.'
        });
    });

    $('#videoFeed').attr('src', finalUrl);

    setCameraStatus({
    status: 'checking',
    message: 'Verificando señal de la cámara...'
    });

    if (streamKind !== 'mediamtx') {
      setTimeout(loadCameraStatus, 1500);
    }
  }

  $('#cameraSelector').on('change', changeCamera);

  $('#fullscreenBtn').on('click', function () {
    const cameraScreen = document.getElementById('cameraScreen');

    if (!cameraScreen) return;

    if (!document.fullscreenElement) {
      cameraScreen.requestFullscreen().catch(err => {
        console.log('No se pudo activar pantalla completa:', err);
      });
    } else {
      document.exitFullscreen();
    }
  });

  $('#snapshotBtn').on('click', function () {
    if (APP_CONFIG.streamMode === 'mediamtx') {
      alert('La captura desde el navegador no está disponible para el reproductor WebRTC.');
      return;
    }

    const img = document.getElementById('videoFeed');

    if (!img) {
      alert('No hay una cámara activa para capturar.');
      return;
    }

    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth || img.clientWidth;
      canvas.height = img.naturalHeight || img.clientHeight;

      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

      const link = document.createElement('a');
      link.download = 'captura-smri.png';
      link.href = canvas.toDataURL('image/png');
      link.click();
    } catch (error) {
      alert('No se pudo capturar la imagen en este momento.');
      console.log(error);
    }
  });

  window.showEventImage = function (imageUrl) {
    $('#modalEventImage').attr('src', imageUrl);
    const modal = new bootstrap.Modal(document.getElementById('eventImageModal'));
    modal.show();
  };

  function loadSecurityEvents() {
    $.ajax({
      url: APP_CONFIG.urls.events,
      method: 'GET',
      success: function (data) {
        renderEvents(data);
      },
      error: function (xhr) {
        console.log('❌ get_security_events error:', xhr.status, xhr.responseText);

        $('#eventsContainer').html(`
          <div class="alert alert-danger m-2">
            Error al cargar eventos.
          </div>
        `);
      }
    });
  }

  function formatEventDetails(details) {
    const text = String(details || 'Sin detalles adicionales').trim();
    const marker = /(Motivo|Descripci[oó]n|Categor[ií]a|Persona|Estado|Evento adicional|Confianza|Nivel)\s*:\s*/gi;
    const normalized = text.replace(marker, '\n$1: ').trim();
    const rows = normalized.split(/\n+/).map(line => line.trim()).filter(Boolean);

    return rows.map(row => {
      const separator = row.indexOf(':');

      if (separator < 0) {
        return `<div class="event-detail-row">${escapeHtml(row)}</div>`;
      }

      const label = row.slice(0, separator).trim();
      const value = row.slice(separator + 1).trim();

      return `
        <div class="event-detail-row">
          <strong>${escapeHtml(label)}:</strong>
          <span>${escapeHtml(value || 'No especificado')}</span>
        </div>
      `;
    }).join('');
  }

  function renderEvents(data) {
    let html = '';
    const events = data.events || [];

    if (events.length) {
      events.forEach(event => {
        let tone = event.priority_class || 'secondary';
        let icon = event.priority_icon || 'fa-info-circle';
        let priority = event.priority_label || event.priority || 'MEDIO';

        if (!event.priority_class) {
          switch (event.event_type) {
            case 'face_recognized':
              tone = 'success';
              icon = 'fa-user-check';
              priority = 'BAJO';
              break;

            case 'face_unknown':
              tone = 'warning';
              icon = 'fa-user-times';
              priority = 'MEDIO';
              break;

            case 'authorized_object':
              tone = 'success';
              icon = 'fa-circle-check';
              priority = 'BAJO';
              break;

            case 'dangerous_object':
            case 'unauthorized_access':
              tone = 'danger';
              icon = 'fa-exclamation-triangle';
              priority = 'ALTO';
              break;
          }
        }

        const title = event.event_type_display || event.event_type || 'Evento';
        const details = event.details || 'Sin detalles adicionales';

        html += `
          <div class="event-card event-card--${tone}">
            <div class="event-main">
              <div class="event-icon">
                <i class="fas ${icon}"></i>
              </div>

              <div class="event-content">
                <strong class="event-title">${escapeHtml(title)}</strong>
                <small class="event-date"><strong>Fecha y hora:</strong> ${escapeHtml(event.timestamp || '')}</small>
                <div class="event-details">${formatEventDetails(details)}</div>
                <small class="event-camera"><i class="fas fa-video"></i> <strong>Cámara:</strong> ${escapeHtml(event.camera || 'Sin cámara')}</small>
              </div>

              <span class="event-priority">${escapeHtml(priority)}</span>
            </div>

            ${event.image_url ? `
              <img
                src="${escapeHtml(event.image_url)}"
                class="event-image-thumb"
                alt="Evidencia"
                onclick="showEventImage('${escapeHtml(event.image_url)}')">
            ` : ''}
          </div>
        `;
      });
    } else {
      html = `
        <div class="loading-box">
          <i class="fas fa-shield-alt fa-2x"></i>
          <span>No hay eventos recientes</span>
        </div>
      `;
    }

    $('#eventsContainer').html(html);
  }

  $('#refreshEvents').on('click', loadSecurityEvents);

  $('#faceImage').on('change', function () {
    const file = this.files[0];

    if (!file) return;

    const reader = new FileReader();

    reader.onload = function (e) {
      $('#facePreview').html(`
        <img src="${e.target.result}" alt="Preview">
      `);
    };

    reader.readAsDataURL(file);
  });

  $('#registerFaceForm').on('submit', function (e) {
    e.preventDefault();

    const nombres = $('#authorizedNombres').val().trim();
    const apellidos = $('#authorizedApellidos').val().trim();
    const celular = $('#authorizedCelular').val().trim();
    const correo = $('#authorizedCorreo').val().trim();
    const cargo = $('#authorizedCargo').val().trim();

    const fileInput = $('#faceImage')[0];

    if (!nombres || !apellidos || !correo || !cargo || !fileInput.files.length) {
      $('#registrationResult').html(`
        <div class="alert alert-danger">
          Completa nombres, apellidos, correo, cargo y selecciona una imagen.
        </div>
      `);
      return;
    }

    const formData = new FormData();

    formData.append('nombres', nombres);
    formData.append('apellidos', apellidos);
    formData.append('celular', celular);
    formData.append('correo', correo);
    formData.append('cargo', cargo);
    formData.append('image', fileInput.files[0]);

    $('#registerBtn')
      .prop('disabled', true)
      .html('<i class="fas fa-spinner fa-spin me-2"></i>Procesando...');

    $('#registrationResult').html('');

    $.ajax({
      url: APP_CONFIG.urls.registerFace,
      type: 'POST',
      data: formData,
      processData: false,
      contentType: false,
      success: function (data) {
        if (data.success === true) {
          $('#registrationResult').html(`
            <div class="alert alert-success">
              ${escapeHtml(data.message || 'Rostro registrado correctamente.')}
            </div>
          `);

          $('#registerFaceForm')[0].reset();

          $('#facePreview').html(`
            <i class="fas fa-check-circle" style="color:#22c55e;"></i>
            <span>Registro completado</span>
          `);
        } else {
          $('#registrationResult').html(`
            <div class="alert alert-danger">
              ${escapeHtml(data.message || 'No se pudo registrar el rostro.')}
            </div>
          `);
        }
      },
      error: function (xhr) {
        console.log('❌ register_face error:', xhr.status, xhr.responseText);

        let message = 'Error en servidor.';

        if (xhr.responseJSON && xhr.responseJSON.message) {
          message = xhr.responseJSON.message;
        } else if (xhr.responseText) {
          try {
            const response = JSON.parse(xhr.responseText);
            message = response.message || response.error || message;
          } catch (e) {
            message = 'Error en servidor.';
          }
        }

        $('#registrationResult').html(`
          <div class="alert alert-danger">
            ${escapeHtml(message)}
          </div>
        `);
      },
      complete: function () {
        $('#registerBtn')
          .prop('disabled', false)
          .html('<i class="fas fa-user-plus me-2"></i>Registrar rostro autorizado');
      }
    });
  });

  let lastLogId = 0;

  function appendLiveLog(items) {
    const box = document.getElementById('liveLog');

    if (!box || !items || !items.length) return;

    items.forEach(it => {
      const row = document.createElement('div');
      const kind = ['danger', 'identity', 'success', 'info'].includes(it.kind)
        ? it.kind
        : 'info';
      const textColors = {
        danger: '#f87171',
        identity: '#60a5fa',
        success: '#4ade80',
        info: '#cbd5e1'
      };

      row.className = `live-log-line live-log-line--${kind}`;
      row.style.color = textColors[kind];

      const time = document.createElement('span');
      time.className = 'live-log-time';
      time.textContent = `[${it.ts}]`;

      const message = document.createElement('span');
      message.className = 'live-log-message';
      message.textContent = it.msg;

      row.append(time, message);
      box.appendChild(row);
    });

    box.scrollTop = box.scrollHeight;

    while (box.children.length > 180) {
      box.removeChild(box.firstElementChild);
    }
  }

  function loadLiveLog() {
    const box = document.getElementById('liveLog');

    if (!box) return;

    const url = box.dataset.url;

    if (!url) return;

    $.ajax({
      url: url,
      method: 'GET',
      data: { after: lastLogId },
      success: function (data) {
        const items = data.lines || [];

        appendLiveLog(items);

        if (typeof data.last_id === 'number') {
          lastLogId = data.last_id;
        } else if (items.length) {
          lastLogId = items[items.length - 1].id;
        }
      },
      error: function (xhr) {
        console.log('❌ live_status error:', xhr.status, xhr.responseText);
      }
    });
  }

  updateTimestamp();
  setInterval(updateTimestamp, 1000);

  loadSecurityEvents();
  setInterval(loadSecurityEvents, APP_CONFIG.eventsRefreshMs);

  loadLiveLog();
  setInterval(loadLiveLog, APP_CONFIG.liveLogRefreshMs);

  changeCamera();
  if (cameraStatusTimer) {
    clearInterval(cameraStatusTimer);
  }
  cameraStatusTimer = setInterval(loadCameraStatus, APP_CONFIG.cameraStatusRefreshMs);
});
