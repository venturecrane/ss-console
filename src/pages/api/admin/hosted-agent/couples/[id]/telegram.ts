import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../../lib/auth/admin-session'
import {
  getCouplesInstance,
  setBotUsername,
  setSharedTelegramChat,
} from '../../../../../../lib/db/couples-onboarding'
import { trimString } from '../../../../../../lib/api/helpers'
import { BOT_USERNAME_RE } from '../../../../../../lib/hosted-agent/couples/link-token'

/**
 * POST /api/admin/hosted-agent/couples/:id/telegram
 *
 * SMD records the two Telegram facts only SMD can know:
 *
 *   bot_username          after creating the couple's bot in BotFather. The
 *                         partners' deep links (t.me/<bot>?start=<token>) need it,
 *                         so the portal's Telegram step stays closed until it is set.
 *   shared_telegram_chat  the shared group's chat id (negative), once the group
 *                         exists with the bot in it. Written into the seat's
 *                         customer.yaml by the generator, never onto a running seat.
 *
 * Either field may be submitted alone; an empty field leaves the stored value.
 */

function back(id: string, st: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `/admin/hosted-agent/couples/${id}?st=${st}` },
  })
}

export const POST: APIRoute = async ({ locals, params, request }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const id = params.id
  if (typeof id !== 'string' || id.length === 0) return back('', 'invalid')
  const instance = await getCouplesInstance(env.DB, id)
  if (!instance) return back(id, 'invalid')

  const form = await request.formData()
  const bot = trimString(form.get('bot_username'))?.replace(/^@/, '') ?? null
  const chat = trimString(form.get('shared_telegram_chat'))
  if (bot === null && chat === null) return back(id, 'invalid')
  if (bot !== null && !BOT_USERNAME_RE.test(bot)) return back(id, 'invalid')
  if (chat !== null && !/^-\d{1,20}$/.test(chat)) return back(id, 'invalid')

  if (bot !== null) await setBotUsername(env.DB, id, bot)
  if (chat !== null) await setSharedTelegramChat(env.DB, id, chat)
  return back(id, 'saved')
}
