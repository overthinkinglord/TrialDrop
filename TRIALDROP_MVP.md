# TrialDrop MVP Spec

Version: v1
Date: 2026-04-22
Status: Ready for implementation

## 1. Product Definition

TrialDrop is a Telegram bot that helps users avoid unwanted charges from free trials.

Core promise:
`You type the service, trial length, and price. The bot reminds you before the charge and tracks how much money you actually saved.`

Positioning:
- Not a finance super-app
- Not a generic reminder bot
- Not an auto-cancel service
- A narrow anti-charge bot for trials and recurring AI subscriptions

## 2. MVP Goal

The MVP succeeds if a new user can:
- start the bot
- add a trial in under 20 seconds
- receive a reminder before the expected charge
- confirm cancellation in one tap
- see a believable saved-money total

## 3. Product Principles

- One primary job: prevent forgotten trial charges
- Telegram-first: no dashboard, no signup, no email gate
- Fast input: one short message should be enough in the happy path
- Honest metrics: saved money is counted only after user confirmation
- Low trust requirement: no bank linking, no card access, no passwords

## 4. Non-Goals for v1

- Auto-detect subscriptions from bank statements
- Auto-cancel subscriptions on behalf of the user
- Web app or admin dashboard for end users
- OCR from screenshots or forwarded receipts
- Full natural-language parsing of any sentence
- Family/team accounts
- Cross-platform billing instructions for every service
- Ads, affiliate flows, or promotion of other products

## 5. Target User

- People who start many AI and SaaS trials
- Users who live in Telegram all day
- Users who do not want another app
- Users who feel pain from forgetting a cancellation once or twice

Primary wedge:
- ChatGPT
- Claude
- Perplexity
- Cursor
- Midjourney
- other common AI tools with trial or near-trial pricing patterns

## 6. Core User Stories

- As a user, I want to send `ChatGPT 14 дней $20` and be done.
- As a user, I want the bot to ask only one follow-up if something is missing.
- As a user, I want a reminder before money is charged.
- As a user, I want to mark the trial as canceled in one tap.
- As a user, I want to see how much money I actually saved.
- As a user, I want to see my active trials at any time.

## 7. Supported Input in v1

Happy-path formats:
- `<service> <duration> <amount>`
- `<service> <renewal date> <amount>`

Examples:
- `ChatGPT 14 дней $20`
- `Claude 7 days $20`
- `Perplexity до 5 мая €20`
- `Cursor renews on May 12 $20`

Supported date concepts:
- relative duration in days
- exact calendar date

Explicitly unsupported in v1:
- voice messages
- screenshots
- long paragraphs with multiple subscriptions
- recurring custom schedules unrelated to trials

## 8. Required Fields for a Saved Trial

- service name
- expected charge date or trial duration
- amount
- currency
- user timezone

Rule:
- If amount or currency is missing, the bot asks a short follow-up.
- If charge date is unclear, the bot asks one short follow-up.
- If timezone is unknown, the bot asks during onboarding before the first save.

## 9. Onboarding Flow

### First-time user

1. User taps `/start`
2. Bot explains the product in one sentence
3. Bot asks for timezone
4. After timezone is set, bot shows one example input
5. User sends first trial

### `/start` copy

`Я помогаю не забывать отменять trial-подписки.`

`Напиши так: ChatGPT 14 дней $20`

`Я напомню до списания и покажу, сколько денег ты спас.`

### Timezone step

Reason:
- Telegram does not reliably provide a user timezone
- reminder delivery time must be user-local

UX:
- quick buttons with common cities and UTC offsets
- free-text fallback allowed

## 10. Conversation Flow

### Happy path

1. User sends `ChatGPT 14 дней $20`
2. Bot parses the input
3. Bot replies with a short confirmation preview
4. User taps `Save`
5. Bot stores the trial and schedules the reminder
6. Bot confirms the reminder date

### Confirmation preview

`Понял так:`

`Сервис: ChatGPT`

`Списание: через 14 дней`

`Сумма: $20`

Buttons:
- `Save`
- `Edit`

### If amount is missing

Bot asks:
`Сколько спишут, если не отменить?`

### If date is missing

Bot asks:
`Когда будет списание или сколько длится trial?`

### If parse confidence is low

Bot does not guess.

Bot replies:
`Не до конца понял формат. Отправь так: ChatGPT 14 дней $20`

## 11. Commands in v1

- `/start` — onboarding and example usage
- `/add` — hint on how to add a trial
- `/list` — active trials
- `/stats` — saved money and current active trials summary
- `/timezone` — change timezone
- `/help` — short command list

No slash command should open a deep menu tree.

## 12. Trial Status Model

Allowed statuses:
- `active`
- `reminder_sent`
- `canceled_confirmed`
- `kept_by_user`
- `expired_no_response`
- `archived`

Status rules:
- new trial starts as `active`
- once primary reminder is sent, status becomes `reminder_sent`
- if user taps `Canceled`, status becomes `canceled_confirmed`
- if user taps `Keep it`, status becomes `kept_by_user`
- if billing moment passes and user did nothing, status becomes `expired_no_response`
- old completed items can later be moved to `archived`

## 13. Reminder Policy

Primary reminder:
- send 24 hours before expected charge

If less than 24 hours remain at creation time:
- if more than 3 hours remain, send at `charge time minus 3 hours`
- if less than 3 hours remain, send immediately after save

Default reminder message:
`Завтра с тебя спишут $20 за ChatGPT. Если уже отменил, нажми ниже.`

Buttons:
- `Canceled`
- `Snooze 3h`
- `Keep it`

After `Snooze 3h`:
- create one extra reminder 3 hours later
- do not allow infinite snoozes in v1

No second automated nag beyond one snooze-generated reminder.

## 14. Saved Money Logic

`Saved money` in v1 means:
- the user confirmed they canceled before the expected charge
- the trial had a valid amount and currency

Rules:
- Count only `canceled_confirmed`
- Do not count `expired_no_response`
- Do not count `kept_by_user`
- Do not count speculative savings

Display concepts:
- `Confirmed saved`
- `Active trials at risk`

Do not show:
- fake “you probably saved”
- inflated yearly projections in v1

## 15. Duplicate Handling

Problem:
- a user may add `ChatGPT` twice by mistake

Detection rule:
- if the same user already has an `active` or `reminder_sent` trial with the same normalized service name, trigger duplicate flow

Duplicate flow message:
`У тебя уже есть активный trial для ChatGPT. Что сделать?`

Buttons:
- `Replace old one`
- `Create another`
- `Cancel`

## 16. Data Model

### users

- `id`
- `telegram_user_id`
- `username`
- `first_name`
- `language_code`
- `timezone`
- `created_at`
- `last_seen_at`

### trials

- `id`
- `user_id`
- `service_name`
- `service_key_normalized`
- `raw_input`
- `amount_minor`
- `currency_code`
- `started_at`
- `billing_at`
- `status`
- `source`
- `created_at`
- `updated_at`

Notes:
- `amount_minor` stores cents-like integer units
- `source` is usually `manual_text` in v1

### reminder_jobs

- `id`
- `trial_id`
- `job_type`
- `scheduled_at`
- `sent_at`
- `status`
- `telegram_message_id`
- `retry_count`
- `created_at`

`job_type` values:
- `primary`
- `snooze`

`status` values:
- `pending`
- `sent`
- `failed`
- `canceled`

### event_log

- `id`
- `user_id`
- `trial_id`
- `event_name`
- `payload_json`
- `created_at`

## 17. Conversational State Machine

User session states:
- `idle`
- `awaiting_timezone`
- `awaiting_amount`
- `awaiting_date`
- `awaiting_duplicate_resolution`
- `awaiting_save_confirmation`

Rules:
- every unfinished state must expire safely
- if the user sends a new valid trial while in a pending state, the new valid input wins
- bot must never trap the user in a dead-end state

## 18. List and Stats UX

### `/list`

Shows:
- service name
- billing date
- amount
- current status

Only active-like statuses should appear by default.

### `/stats`

Shows:
- confirmed saved total
- count of active trials
- next upcoming charge

Suggested copy:
`Ты уже спас $60.`

`Сейчас активных trial: 3.`

`Ближайшее списание: Claude, завтра, $20.`

## 19. Reliability Requirements

- reminder jobs must survive process restart
- webhook updates must be idempotent
- reminder sending must tolerate retries
- duplicate button taps must not duplicate side effects
- the same reminder must not be sent twice after a retry race

## 20. Analytics Events

Track these from day one:
- `user_started`
- `timezone_set`
- `trial_parse_succeeded`
- `trial_parse_failed`
- `trial_saved`
- `duplicate_detected`
- `reminder_sent`
- `reminder_failed`
- `trial_canceled_confirmed`
- `trial_kept`
- `trial_snoozed`
- `stats_viewed`

Primary funnel:
- start
- first trial saved
- reminder delivered
- cancellation confirmed

## 21. Service Normalization

Need a simple normalization layer for common names:
- `chatgpt`
- `gpt`
- `openai chatgpt`
- `claude`
- `cursor`
- `perplexity`

Purpose:
- duplicate detection
- cleaner stats
- future service-specific cancel instructions

This is a lightweight alias map, not a heavy taxonomy.

## 22. Tone of Voice

Voice:
- fast
- useful
- calm
- not salesy
- not “finance guru”

Good:
- `Запомнил. Напомню за день до списания.`
- `Готово. Ты успел отменить до списания.`

Bad:
- `Congratulations on optimizing your subscription lifecycle`
- `We estimate huge annual savings`

## 23. Core Message Templates

### Save confirmation

`Готово. Trial сохранен.`

`Напомню 11 мая в 10:00 по твоему времени.`

### Reminder

`Завтра спишут $20 за ChatGPT. Успевай отменить, если больше не нужно.`

### After canceled

`Отлично. Засчитал как спасенные деньги: $20.`

### After keep

`Ок, не напоминаю. Этот trial не пойдет в saved money.`

### After snooze

`Хорошо. Напомню еще раз через 3 часа.`

## 24. Security and Privacy Rules

- store only the data needed to deliver the service
- never ask for payment credentials
- never ask for service passwords
- never claim the bot canceled something automatically if it did not
- keep raw input for debugging, but avoid collecting unrelated personal data

## 25. Admin Needs for Launch

- health check for bot process
- visibility into due reminder jobs
- visibility into failed reminder sends
- simple query for top services added
- ability to manually inspect one user’s trial history

No user-facing admin panel is needed in v1.

## 26. Launch Scope

v1 includes:
- Telegram bot
- manual trial entry
- reminder delivery
- one-tap status actions
- active trial list
- saved-money stats

v1 does not include:
- cancel instructions per service
- multilingual localization beyond the initial core language
- viral loops
- referral mechanics
- premium upsell

## 27. Recommended Build Order

1. onboarding and timezone
2. parser for happy-path manual input
3. save confirmation flow
4. persistent trial storage
5. reminder scheduler
6. reminder action buttons
7. `/list` and `/stats`
8. analytics events
9. duplicate protection
10. cleanup and polish

## 28. Definition of Done

- new user can complete onboarding in under 30 seconds
- first trial can be saved from one message in the happy path
- missing fields trigger only short focused follow-ups
- reminders persist across deploys and restarts
- reminder buttons update state correctly
- saved money increases only after confirmed cancellation
- `/list` and `/stats` reflect the real current state
- duplicate adds do not silently create messy state
- failed reminder sends are visible in logs
- no core bot flow requires a web UI

## 29. Phase 2 After MVP

- service-specific cancel instructions
- forwarded receipt parsing
- screenshot OCR
- shareable monthly savings card
- growth hooks and native promotion of other products
