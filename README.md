# Zont Sensor Bridge

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1.0%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Donate](https://img.shields.io/badge/Donate-Support-green?style=flat-square&logo=githubsponsors)](https://pay.cloudtips.ru/p/0b1867f6)

Однонаправленный мост из Home Assistant в термостат Zont: читает показания выбранных датчиков температуры и отправляет их на термостат через локальное WebSocket-соединение. Собственных сущностей-датчиков интеграция не создаёт.

## Возможности

- Отправка температуры при изменении состояния датчика и периодическая повторная отправка (настраиваемый интервал)
- Список датчиков Zont (тип 1) загружается с устройства — сопоставление по имени, без ручного ввода ID
- Преобразование °F → °C, ограничение min/max, пропуск состояний `unknown`/`unavailable`
- Автопереподключение с экспоненциальной задержкой (5–60 с), WebSocket heartbeat 30 с, повторная авторизация
- Конфигурация через UI (config flow + options flow), сервис `send_temperature`, устройство в реестре HA
- Английский и русский переводы интерфейса

## Требования

- Home Assistant 2024.1.0 или новее
- Термостат Zont с WebSocket-эндпоинтом `/ws` (например, `ws://192.168.254.85/ws`)
- Дополнительные зависимости не требуются

## Установка

**HACS:** добавьте репозиторий как custom repository (категория: integration), установите **Zont Sensor Bridge** и перезапустите Home Assistant.

**Вручную:** скопируйте `custom_components/zont_sensor_bridge/` в `<ha-config>/custom_components/` и перезапустите Home Assistant.

Затем откройте **Settings → Devices & Services → Add Integration → Zont Sensor Bridge**.

## Настройка

1. **Устройство** — URL WebSocket (`ws://…/ws`), имя пользователя (по умолчанию `admin`), пароль, опционально HTTP Basic Auth.
2. **Интервал обновления** — как часто (в секундах) повторно отправляются все значения. По умолчанию 60.
3. **Датчики** — для каждого сопоставления выберите датчик температуры Home Assistant и датчик Zont из загруженного списка; при необходимости задайте min/max.

## Сервис

```yaml
service: zont_sensor_bridge.send_temperature
data:
  device_id: <device>
  zont_id: 1
  temperature: 21.5
```

## Примечания

- Локальный WebSocket API Zont не документирован публично; протокол проверен через `wscat` и официальную документацию Zont MQTT.
- Температура всегда отправляется в °C; показания в других единицах конвертируются автоматически.
