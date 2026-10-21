export interface RequestOptions { idempotencyKey?: string }
export type Obj = Record<string, any>;

export interface Resource {
  create(params: Obj, opts?: RequestOptions): Promise<Obj>;
  retrieve(id: string): Promise<Obj>;
  list(query?: Obj): Promise<{ data: Obj[]; has_more?: boolean }>;
  action(id: string, name: string, params?: Obj): Promise<Obj>;
}

export class SangamPayError extends Error {
  status: number; code: string; param?: string;
}

export class SangamPay {
  constructor(secretKey: string, opts?: { baseUrl?: string; timeoutMs?: number; maxRetries?: number });
  paymentIntents: Resource & {
    confirm(id: string, method: "upi" | "netbanking" | "card" | "bank_debit" | "paypal", methodData?: Obj): Promise<Obj>;
    cancel(id: string): Promise<Obj>;
    verify(id: string, approve?: boolean, note?: string): Promise<Obj>;
  };
  refunds: Resource;
  customers: Resource;
  plans: Resource;
  subscriptions: Resource & { cancel(id: string, atPeriodEnd?: boolean): Promise<Obj> };
  invoices: Resource & { void(id: string): Promise<Obj>; markPaid(id: string, note?: string): Promise<Obj> };
  paymentLinks: Resource;
  payouts: Resource;
  bankAccounts: Resource;
  disputes: Resource;
  webhookEndpoints: Resource;
  events: Resource;
  reconcile(csv: string, autoApprove?: boolean): Promise<Obj>;
  analytics(days?: number): Promise<Obj>;
  balance(): Promise<Obj>;
  request(method: string, path: string, body?: Obj, opts?: RequestOptions): Promise<Obj>;
}

export function verifyWebhook(rawBody: Buffer | string, signatureHeader: string, secret: string, toleranceSeconds?: number): Obj;
export const VERSION: string;
