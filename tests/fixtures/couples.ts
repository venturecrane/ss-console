/** A complete, valid couples joint section, shared by the couples tests. */
import type { JointSettings } from '../../src/lib/hosted-agent/couples/joint-settings'

export const JOINT: JointSettings = {
  timezone: 'America/Phoenix',
  checkin_topics: ['how the week felt', 'money', 'time together'],
  checkin: {
    p1: { days: ['mon', 'thu'], time: '18:30' },
    p2: { days: ['tue'], time: '19:00' },
  },
  gratitude: {
    p1: { days: ['wed'], time: '20:00' },
    p2: { days: ['wed'], time: '20:15' },
  },
  daily_reminder_time: '08:00',
  weekly_checkin: { day: 'sun', time: '19:00' },
  quiet_hours: { start: '21:00', end: '08:00' },
  preferred_channel: { p1: 'email', p2: 'telegram' },
}
