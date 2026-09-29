# Cartelera Mérida — próximos 3 meses

Repositorio basado en el formato de `agenda-semanal-cultural`, pero convertido
en un **gestor de cartelera de espectáculos**.

## Qué hace

Cada ejecución calcula automáticamente:

**hoy → misma fecha de dentro de 3 meses**

Con fecha 28/09/2026, por ejemplo:

**28/09/2026 → 28/12/2026**

### Incluye únicamente

- 🎭 Teatro
- 🎼 Musical
- 🎬 Cine comercial y ciclos/cine de autor/VOSE
- 🎤 Monólogos / stand-up
- 🎵 Conciertos y giras musicales
- 💃 Danza

### Excluye

Exposiciones, conferencias, congresos, jornadas, talleres, cursos,
actividades deportivas, actos institucionales y actividades infantiles o
escolares de barrio que no sean un espectáculo escénico propiamente dicho.

## Fuentes

1. Ayuntamiento de Mérida / API oficial de agenda
2. Teatro María Luisa
3. Palacio de Congresos de Mérida
4. Cines Victoria Mérida
5. Cine Club Fórum Mérida

Las fuentes de ticketing se conservan mediante el enlace oficial de cada
evento/ficha cuando está disponible. Así el mensaje no depende de una tienda
concreta si el promotor cambia de plataforma (Entradas.com, Giglon,
Tomaticket, El Corte Inglés, etc.).

## Formato Telegram

La salida se agrupa por:

- Mes 1
- Mes 2
- Mes 3

Cada evento muestra:

**Fecha y hora · título · género · recinto · Entradas/info**

El bot mantiene un máximo de 4 mensajes por ejecución.

## Instalación

1. Crear un repositorio nuevo, por ejemplo `cartelera-merida-3-meses`.
2. Subir los archivos del repositorio.
3. En GitHub → Settings → Secrets and variables → Actions:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` o `TELEGRAM_CHAT_IDS`
4. Ejecutar Actions → `Cartelera Mérida 3 meses → Telegram`.
5. El workflow vuelve a ejecutarse automáticamente cada día.

## Pruebas

```bash
pip install -r requirements.txt
python -m pytest -q
```

## Nota sobre la cartelera de cine

La cartelera comercial cambia con mucha más rapidez que la programación
teatral. Por eso Cines Victoria se consulta en cada ejecución y solo se
incluyen sesiones/fechas que la web publica realmente en ese momento.

## Formato Telegram

La salida se organiza estrictamente por recinto y, dentro de cada recinto, por título/producción. El orden de recintos es:

1. 🎬 Cines Victoria
2. 📍 Palacio de Congresos
3. 🎭 Teatro María Luisa
4. 🏛️ Teatro Romano
5. 📍 Centro Cultural Alcazaba
6. 🎬 Cineclub Fórum
7. 📍 Otros recintos

Las sesiones del mismo título se agrupan bajo una única entrada y se ordenan cronológicamente. En cine se muestra de forma compacta título + sesiones + enlace. En teatro, musicales, monólogos, conciertos y danza se añade una descripción breve cuando la fuente la proporciona.

Los enlaces priorizan la venta/taquilla detectada en la ficha oficial y, como respaldo, el enlace oficial del recinto. El envío mantiene un máximo de 4 mensajes por ejecución y puede cortar un recinto entre mensajes cuando sea necesario, sin alterar el orden de la cartelera.
