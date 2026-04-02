/**
 * Technology stack detection via HTML regex scanning.
 * No external API calls — pure pattern matching.
 */

export interface TechStackResult {
  scheduling: string[]
  crm: string[]
  reviews: string[]
  payments: string[]
  communication: string[]
  platform: string[]
  analytics: string[]
}

interface ToolPattern {
  name: string
  category: keyof TechStackResult
  patterns: RegExp[]
}

const TOOL_PATTERNS: ToolPattern[] = [
  // Scheduling
  { name: 'ServiceTitan', category: 'scheduling', patterns: [/servicetitan/i, /st-booking/i] },
  { name: 'Jobber', category: 'scheduling', patterns: [/jobber\.com/i, /getjobber/i] },
  { name: 'Housecall Pro', category: 'scheduling', patterns: [/housecallpro/i] },
  { name: 'Calendly', category: 'scheduling', patterns: [/calendly\.com/i, /assets\.calendly/i] },
  { name: 'Acuity', category: 'scheduling', patterns: [/acuityscheduling/i] },
  { name: 'ScheduleEngine', category: 'scheduling', patterns: [/scheduleengine/i] },
  // CRM
  { name: 'HubSpot', category: 'crm', patterns: [/hs-script/i, /hubspot\.com/i, /hbspt/i] },
  { name: 'Salesforce', category: 'crm', patterns: [/salesforce\.com/i, /force\.com/i] },
  { name: 'Zoho', category: 'crm', patterns: [/zoho\.com/i, /zsiqchat/i] },
  {
    name: 'GoHighLevel',
    category: 'crm',
    patterns: [/gohighlevel/i, /highlevel/i, /msgsndr/i],
  },
  // Reviews
  { name: 'Podium', category: 'reviews', patterns: [/podium\.com/i, /connect\.podium/i] },
  { name: 'Birdeye', category: 'reviews', patterns: [/birdeye\.com/i] },
  { name: 'NiceJob', category: 'reviews', patterns: [/nicejob\.co/i] },
  // Payments
  { name: 'Square', category: 'payments', patterns: [/squareup\.com/i, /square\.site/i] },
  { name: 'Stripe', category: 'payments', patterns: [/stripe\.com\/v3/i, /js\.stripe/i] },
  { name: 'PayPal', category: 'payments', patterns: [/paypal\.com/i, /paypalobjects/i] },
  // Communication
  { name: 'Twilio', category: 'communication', patterns: [/twilio\.com/i] },
  { name: 'SimpleTexting', category: 'communication', patterns: [/simpletexting/i] },
  { name: 'Intercom', category: 'communication', patterns: [/intercom\.io/i, /intercomcdn/i] },
  // Platform
  { name: 'WordPress', category: 'platform', patterns: [/wp-content/i, /wp-includes/i] },
  { name: 'Wix', category: 'platform', patterns: [/wix\.com/i, /wixstatic/i] },
  { name: 'Squarespace', category: 'platform', patterns: [/squarespace\.com/i, /sqsp/i] },
  { name: 'GoDaddy', category: 'platform', patterns: [/godaddy\.com/i, /secureserver/i] },
  { name: 'Weebly', category: 'platform', patterns: [/weebly\.com/i] },
  // Analytics
  {
    name: 'Google Analytics',
    category: 'analytics',
    patterns: [/google-analytics/i, /gtag/i, /googletagmanager/i],
  },
  {
    name: 'Facebook Pixel',
    category: 'analytics',
    patterns: [/fbevents/i, /connect\.facebook/i, /fbq\(/i],
  },
  { name: 'Hotjar', category: 'analytics', patterns: [/hotjar\.com/i] },
]

export function detectTechStack(html: string): TechStackResult {
  const result: TechStackResult = {
    scheduling: [],
    crm: [],
    reviews: [],
    payments: [],
    communication: [],
    platform: [],
    analytics: [],
  }

  for (const tool of TOOL_PATTERNS) {
    if (tool.patterns.some((p) => p.test(html))) {
      result[tool.category].push(tool.name)
    }
  }

  return result
}
