"""A30-12 - il booking pubblico a link singolo-agente.

Pacchetto NUOVO, additivo. Non importa mai `appointments` per scrivere un
appuntamento: l'unico punto che inserisce in `appointments` e'
`appointments.service.create_public_booking_appointment` (A30-12B,
CORREZIONE ARCHITETTURALE OBBLIGATORIA) - questo pacchetto lo CHIAMA, non lo
sostituisce e non aggira il suo repository.
"""
