document.body.addEventListener('radiotomateMessage', function(evt) {
    Swal.fire(evt.detail);
});

document.body.addEventListener('htmx:configRequest', function(evt) {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
        evt.detail.headers['X-CSRF-Token'] = meta.content;
    }
});

// we exploit the fact that hx-on makes the event available as a global
// but be careful that it's not available in the then() callback!
function confirmWithMessage(message) {
    event.preventDefault();
    var onConfirmed = event.detail.issueRequest;
    Swal.fire({
        title: message,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: 'Oui',
        cancelButtonText: 'Annuler',
        reverseButtons: true
    }).then((result) => {
        if (result.isConfirmed) {
            onConfirmed();
        }
    });
}
