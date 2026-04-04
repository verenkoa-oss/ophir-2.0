# GitHub Limits — Руководство для одиночного разработчика
# GitHub Limits — Guide for a Solo Developer

> Репозиторий: **verenkoa-oss/ophir-2.0** | Аккаунт: **GitHub Free** + **Copilot Pro (trial)**  
> Repository: **verenkoa-oss/ophir-2.0** | Account: **GitHub Free** + **Copilot Pro (trial)**

---

## 🇷🇺 Часть 1 — Какие лимиты могут появиться БЕЗ Actions

Даже без файлов `.github/workflows/` и без запуска Actions, GitHub автоматически включает
ряд сервисов, у которых есть свои квоты:

### 1.1 Copilot Premium Requests (самая вероятная причина)

| Что | Лимит (GitHub Free + Copilot Pro) |
|-----|-----------------------------------|
| Быстрые запросы (GPT-4o, Claude Sonnet) | 300 premium-запросов в месяц |
| После исчерпания лимита | Переход на базовую (не-premium) модель или сообщение "You've used all of this month's Premium requests" |

**Это НЕ влияет на git push/pull/merge.**  
Это только ограничение чата/AI-дополнений Copilot.

---

### 1.2 Автоматические проверки безопасности (Code Security)

GitHub может автоматически включить следующее без явного запроса пользователя:

| Сервис | Что делает | Лимит |
|--------|-----------|-------|
| **Dependabot alerts** | Предупреждения об уязвимых зависимостях в requirements.txt | Бесплатно, но создаёт алерты |
| **Secret scanning** | Автоматически сканирует коммиты на наличие токенов/ключей | Бесплатно для всех |
| **Push protection** | Может ЗАБЛОКИРОВАТЬ push, если находит секрет (API-ключ, токен) в коде | Бесплатно, включается автоматически |
| **Code scanning (CodeQL)** | Анализ кода на уязвимости (нужен `.github/workflows/`) | 0 мин → не работает без workflows |

> ⚠️ **Push protection** — единственный из этих сервисов, который может реально
> заблокировать `git push`. Если в коммите есть строка, похожая на API-ключ или
> токен — GitHub заблокирует push и покажет предупреждение.

---

### 1.3 Лимиты на размер репозитория и трафик

| Тип | Лимит (GitHub Free) |
|-----|---------------------|
| Размер репозитория (рекомендованный) | до 1 ГБ |
| Размер репозитория (жёсткий) | 5 ГБ (после — письмо от GitHub) |
| Git LFS хранилище | 1 ГБ бесплатно |
| Git LFS трафик (bandwidth) | 1 ГБ в месяц бесплатно |
| Размер одного файла | до 100 МБ (выше — push отклоняется) |
| GitHub API rate limit | 60 req/час (без токена), 5 000 req/час (с токеном) |

> Обычные push/pull с кодом (Python-файлы, JSON, HTML) занимают килобайты —
> лимиты хранилища не достигаются при нормальной разработке.

---

### 1.4 Лимиты GitHub Actions (не задействованы, но важно знать)

| Тип | GitHub Free |
|-----|-------------|
| Минуты Actions в месяц | 2 000 мин (публичные репо — **безлимитно**) |
| Пакеты (Packages storage) | 500 МБ |
| Codespaces | 120 core-hours / месяц |

В репозитории **нет `.github/workflows/`** → Actions не запускаются → минуты не тратятся.

---

## 🇷🇺 Часть 2 — Как понять, что вызвало лимит

### Шаг 1 — Прочитать точный текст сообщения

| Текст сообщения | Причина |
|-----------------|---------|
| "You've used all of this month's Premium requests" | Исчерпан лимит Copilot premium-запросов |
| "Push rejected: secret scanning" / "Push blocked" | Push protection заблокировал токен/ключ в коде |
| "This repository is over its data quota" | Превышен лимит размера репозитория |
| "File exceeds GitHub's file size limit" | Файл > 100 МБ |
| "You have exceeded a secondary rate limit" | Слишком частые API-запросы |
| "Actions minutes limit reached" | Исчерпаны минуты Actions |

### Шаг 2 — Проверить детали использования

1. Перейди на: **https://github.com/settings/billing/summary**
2. Раздел **"Usage this month"** покажет:
   - Copilot запросы (сколько использовано из лимита)
   - Actions минуты (0, если нет workflows)
   - Storage (если есть LFS)
3. Раздел **"Spending limits"**: https://github.com/settings/billing/spending_limits

### Шаг 3 — Проверить алерты безопасности репозитория

1. Перейди на: **https://github.com/verenkoa-oss/ophir-2.0/security**
2. Здесь увидишь:
   - Dependabot alerts (уязвимые зависимости)
   - Secret scanning alerts (найденные секреты)
   - Code scanning results (если включено)

---

## 🇷🇺 Часть 3 — Что отключить или игнорировать при повторном лимите

### 3.1 Если сообщение о Copilot Premium requests

**Действие:** Просто продолжай пользоваться — Copilot переключится на базовую модель
(базовую, не-premium модель), которая работает без ограничений. Или подожди начала следующего месяца.

**Это не влияет на код и git.**

---

### 3.2 Если push заблокирован (Push Protection / Secret Scanning)

GitHub заблокировал push, потому что нашёл в коде строку, похожую на API-ключ.

**Варианты:**
1. **Убери секрет из кода** (замени на переменную окружения или файл `.env`)
2. **Разреши push вручную** (если это ложное срабатывание):
   - GitHub покажет ссылку типа: `https://github.com/verenkoa-oss/ophir-2.0/security/secret-scanning/unblock-secret/...`
   - Нажми "Allow secret" → push пройдёт
3. **Отключить Push Protection** (не рекомендуется, но возможно):
   - Settings → Code security → Secret scanning → Push protection → **Disable**

---

### 3.3 Отключение автоматических проверок безопасности

Если хочешь отключить автоматические сканирования:

1. Перейди: **https://github.com/verenkoa-oss/ophir-2.0/settings/security_analysis**
2. Там можно отключить:
   - **Dependabot alerts** — уведомления об уязвимостях (отключить "Dependabot alerts")
   - **Secret scanning** — сканирование секретов (отключить "Secret scanning")
   - **Push protection** — блокировка при обнаружении секретов (отключить "Push protection")

> 💡 Dependabot alerts и secret scanning (без push protection) только показывают
> предупреждения — они НЕ блокируют работу. Их можно оставить включёнными.

---

### 3.4 Если превышен лимит файла (100 МБ)

```bash
# Проверить большие файлы в репозитории
git ls-files | xargs ls -lh | sort -k5 -rh | head -20
```

Если нужно хранить большие файлы (бинарники, датасеты) — используй Git LFS:
```bash
git lfs track "*.db"
git lfs track "*.bin"
git add .gitattributes
```

---

## 🇷🇺 Часть 4 — Подтверждение: git push/pull не ограничены

### ✅ Операции, которые ВСЕГДА бесплатны и безлимитны

| Операция | Лимит |
|----------|-------|
| `git push` | ♾️ Безлимитно |
| `git pull` | ♾️ Безлимитно |
| `git fetch` | ♾️ Безлимитно |
| `git clone` | ♾️ Безлимитно |
| `git merge` | ♾️ Безлимитно (это локальная операция) |
| Создание issues | ♾️ Безлимитно |
| Создание pull requests | ♾️ Безлимитно |
| Создание веток | ♾️ Безлимитно |
| Публичные и приватные репозитории | ♾️ Безлимитно (GitHub Free) |
| Коллабораторы | ♾️ Безлимитно (GitHub Free) |

### ❌ НЕТ лимита на git для одного разработчика

GitHub **не ограничивает** количество:
- push-операций в день/месяц
- коммитов
- веток
- репозиториев (публичных и приватных)

Даже на **GitHub Free** — всё это бесплатно и безлимитно.

---

## Краткий итог / Quick Reference

| Вопрос | Ответ |
|--------|-------|
| Может ли GitHub Free ограничить git push? | ❌ Нет, никогда |
| Что за "лимит" видел пользователь? | ✅ Скорее всего Copilot Premium запросы |
| Нужны ли Actions для личного SDR проекта? | ❌ Нет |
| Влияет ли Copilot Pro ($10) на git лимиты? | ❌ Нет, только AI-фичи |
| Как проверить использование? | https://github.com/settings/billing/summary |
| Как отключить авто-сканирования? | https://github.com/verenkoa-oss/ophir-2.0/settings/security_analysis |

---

## 🇬🇧 English Summary

**What limits can appear without Actions:**
- **Copilot premium requests** (300/month): most likely cause of "limit" message. Does not affect git.
- **Push protection**: can block `git push` if GitHub detects a secret/token in the code. Bypass via the provided link or remove the secret.
- **Repository size**: soft limit 1 GB, hard limit 5 GB. Unlikely for a normal code project.
- **Single file size**: max 100 MB per file.

**Git push/pull/merge are always free and unlimited** on GitHub Free for any number of users,
repositories, and branches.

**To check what triggered a limit:** https://github.com/settings/billing/summary

**To disable auto-security scans:** https://github.com/verenkoa-oss/ophir-2.0/settings/security_analysis

---

*Document created: 2026-04-03 | Repository: verenkoa-oss/ophir-2.0*
