// Espera a que el DOM esté completamente cargado
document.addEventListener('DOMContentLoaded', function() {
    // Gráfico de líneas
    const ctxLine = document.getElementById('activity-chart');
    if (ctxLine) {
      new Chart(ctxLine, {
        type: 'line',
        data: {
          labels: ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'],
          datasets: [{
            label: 'Eventos',
            data: [12, 19, 8, 15, 22, 17, 25],
            borderColor: '#f5365c',
            backgroundColor: 'rgba(245, 54, 92, 0.1)',
            borderWidth: 2,
            tension: 0.4,
            fill: true,
            pointBackgroundColor: '#fff',
            pointRadius: 4,
            pointHoverRadius: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function(context) {
                  return context.parsed.y + ' eventos';
                }
              }
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: {
                callback: function(value) {
                  return value + (value === 1 ? ' evento' : ' eventos');
                }
              }
            }
          }
        }
      });
    }
  
    // Gráfico circular
    const ctxPie = document.getElementById('event-distribution-chart');
    if (ctxPie) {
      new Chart(ctxPie, {
        type: 'doughnut',
        data: {
          labels: ['Movimiento', 'Sonido', 'Acceso', 'Otros'],
          datasets: [{
            data: [35, 25, 20, 20],
            backgroundColor: ['#f5365c', '#fb6340', '#11cdef', '#5e72e4'],
            borderWidth: 0,
            hoverOffset: 10
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '70%',
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function(context) {
                  return context.label + ': ' + context.raw + '%';
                }
              }
            }
          }
        }
      });
    }
  });