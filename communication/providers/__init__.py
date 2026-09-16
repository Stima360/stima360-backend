"""P29-2.4 - il confine verso i provider, e in questa fase nient'altro.

QUI NON C'E' NESSUN PROVIDER REALE

P29-2.4 costruisce il dispatcher e il gate del consenso, non gli adapter:
quelli sono P29-2.5, e avvolgeranno `database.invia_mail` e le due funzioni
WhatsApp senza riscriverle. Cio' che vive qui oggi e' la FORMA di un esito -
`ProviderResult`, `ProviderCapabilities` - e un provider finto che non chiama
nessuno.

IL PACCHETTO E' IMPORTABILE SOLO DAL DISPATCHER

E' la prima delle tre sentinelle del design (§12.2). Non e' una convenzione: un
test elenca chi importa questo pacchetto e rifiuta qualunque nome diverso da
`communication/dispatcher.py`. Un service che potesse importare un provider
potrebbe mandare un messaggio senza passare dal gate del consenso, ed e'
precisamente cio' che il gate esiste per impedire.
"""
