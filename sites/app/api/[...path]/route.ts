import { env } from "cloudflare:workers";
import { handle } from "../../../lib/api.mjs";
export const dynamic = "force-dynamic";
export async function GET(request:Request) { return handle(request, env); }
export async function POST(request:Request) { return handle(request, env); }
