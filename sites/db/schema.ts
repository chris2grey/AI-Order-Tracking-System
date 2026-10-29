import { sqliteTable, text, integer, check } from "drizzle-orm/sqlite-core";
import { sql } from "drizzle-orm";
export const accounts = sqliteTable("accounts", {
  id: text("id").primaryKey(), username: text("username").notNull().unique(),
  password: text("password").notNull(), salt: text("salt").notNull(),
  role: text("role").notNull(), active: integer("active").notNull().default(1),
  version: integer("version").notNull().default(1), created: text("created").notNull(),
});
export const orders = sqliteTable("orders", {
  id: text("id").primaryKey(), number: text("number").notNull().unique(),
  customer: text("customer").notNull(), owner: text("owner").notNull(),
  state: text("state").notNull(), version: integer("version").notNull().default(1),
  updated: text("updated").notNull(),
});
export const mutations = sqliteTable("mutations", {
  key: text("key").primaryKey(), user: text("user").notNull(),
  operation: text("operation").notNull(), digest: text("digest").notNull(),
  response: text("response").notNull(), orderId: text("order_id"),
  ok: integer("ok").notNull(), created: text("created").notNull(),
}, t => [check("mutation_committed_once", sql`${t.ok} = 1`)]);
export const loginAttempts = sqliteTable("login_attempts", {
  key: text("key").primaryKey(), attempts: integer("attempts").notNull(),
  expires: integer("expires").notNull(),
});
