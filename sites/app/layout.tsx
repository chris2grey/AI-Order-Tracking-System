import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title:"AI 跟单系统",description:"订单、收付款与分批发货工作台"};
export default function RootLayout({children}:{children:React.ReactNode}) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}