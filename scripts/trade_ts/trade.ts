import { ClobClient, Side, OrderType } from "@polymarket/clob-client";
import { Wallet } from "ethers";
import * as dotenv from "dotenv";
import * as path from "path";

// Load .env from parent directory
dotenv.config({ path: path.resolve(__dirname, "../../.env") });

const HOST = "https://clob.polymarket.com";
const GAMMA_API = "https://gamma-api.polymarket.com";
const CHAIN_ID = 137;

interface MarketInfo {
  yesToken: string;
  noToken: string;
  yesPrice: number;
  noPrice: number;
  question: string;
}

async function getBtcHourlyMarket(): Promise<MarketInfo | null> {
  const now = new Date();
  // Convert to ET
  const etTime = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));

  for (let offset = 0; offset <= 1; offset++) {
    const dt = new Date(etTime.getTime() + offset * 60 * 60 * 1000);
    const month = dt.toLocaleString("en-US", { month: "long" }).toLowerCase();
    const day = dt.getDate();
    const hour = dt.getHours();

    let hourStr: string;
    if (hour === 0) hourStr = "12am";
    else if (hour < 12) hourStr = `${hour}am`;
    else if (hour === 12) hourStr = "12pm";
    else hourStr = `${hour - 12}pm`;

    const slug = `bitcoin-up-or-down-${month}-${day}-${hourStr}-et`;
    console.log(`Trying: ${slug}`);

    try {
      const resp = await fetch(`${GAMMA_API}/events?slug=${slug}`);
      const events = await resp.json();

      if (events && events.length > 0) {
        const event = events[0];
        console.log(`Found: ${event.title}`);

        for (const market of event.markets || []) {
          if (market.closed) continue;

          let tokens = market.clobTokenIds;
          if (typeof tokens === "string") tokens = JSON.parse(tokens);

          let prices = market.outcomePrices;
          if (typeof prices === "string") prices = JSON.parse(prices);

          if (tokens && tokens.length >= 2) {
            return {
              yesToken: tokens[0],
              noToken: tokens[1],
              yesPrice: parseFloat(prices?.[0] || "0.5"),
              noPrice: parseFloat(prices?.[1] || "0.5"),
              question: market.question || "",
            };
          }
        }
      }
    } catch (e) {
      console.error(`Error fetching ${slug}:`, e);
    }
  }

  return null;
}

async function main() {
  console.log("=".repeat(60));
  console.log("POLYMARKET BTC HOURLY TRADER (TypeScript)");
  console.log("=".repeat(60));

  const privateKey = process.env.POLY_PRIVATE_KEY || "";
  const safeAddress = process.env.POLY_SAFE_ADDRESS || "";
  const sigType = parseInt(process.env.POLY_SIGNATURE_TYPE || "2");

  const pk = privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`;

  console.log(`\nSafe (funder): ${safeAddress}`);
  console.log(`Signature type: ${sigType}`);

  // Get market
  const market = await getBtcHourlyMarket();
  if (!market) {
    console.log("No BTC hourly market found!");
    return;
  }

  console.log(`\nMarket: ${market.question}`);
  console.log(`YES price: $${market.yesPrice.toFixed(2)}`);
  console.log(`NO price: $${market.noPrice.toFixed(2)}`);

  // Initialize client
  console.log("\nInitializing client...");
  const signer = new Wallet(pk);

  const client = new ClobClient(
    HOST,
    CHAIN_ID,
    signer,
    undefined, // creds will be derived
    sigType,
    safeAddress
  );

  // Get or create API credentials
  console.log("Getting API credentials...");
  const creds = await client.createOrDeriveApiKey();
  console.log(`API Key: ${creds.apiKey.substring(0, 20)}...`);

  // Reinitialize with credentials
  const authedClient = new ClobClient(
    HOST,
    CHAIN_ID,
    signer,
    creds,
    sigType,
    safeAddress
  );

  // Get market info for tick_size
  console.log("\nFetching market info...");
  let tickSize = "0.01";
  let negRisk = true;
  try {
    const marketInfo = await authedClient.getMarket(market.yesToken);
    tickSize = marketInfo.minimum_tick_size || "0.01";
    negRisk = marketInfo.neg_risk || true;
    console.log(`Tick size: ${tickSize}`);
    console.log(`Neg risk: ${negRisk}`);
  } catch (e) {
    console.log(`Could not get market info: ${e}`);
  }

  // Calculate prices
  let yesBid = Math.round((market.yesPrice - 0.02) * 100) / 100;
  let noBid = Math.round((market.noPrice - 0.02) * 100) / 100;

  if (yesBid + noBid > 0.96) {
    yesBid = 0.47;
    noBid = 0.47;
  }

  yesBid = Math.max(0.01, yesBid);
  noBid = Math.max(0.01, noBid);

  const size = 5;

  console.log("\n" + "=".repeat(60));
  console.log("ORDER PLAN");
  console.log("=".repeat(60));
  console.log(`YES BUY: ${size} @ $${yesBid.toFixed(2)}`);
  console.log(`NO BUY:  ${size} @ $${noBid.toFixed(2)}`);
  console.log(`Total: $${(yesBid + noBid).toFixed(2)}`);
  console.log(`Profit if both fill: $${(1 - yesBid - noBid).toFixed(2)}`);

  console.log("\n" + "=".repeat(60));
  console.log("PLACING ORDERS");
  console.log("=".repeat(60));

  // Place YES order
  console.log("\nPlacing YES order...");
  try {
    const yesResponse = await authedClient.createAndPostOrder({
      tokenID: market.yesToken,
      price: yesBid,
      size: size,
      side: Side.BUY,
    }, {
      tickSize: tickSize,
      negRisk: negRisk,
    }, OrderType.GTC);

    console.log(`YES Order Response:`, yesResponse);
    if (yesResponse.orderID) {
      console.log(`✅ YES Order placed: ${yesResponse.orderID}`);
    }
  } catch (e: any) {
    console.log(`❌ YES Error: ${e.message || e}`);
  }

  // Place NO order
  console.log("\nPlacing NO order...");
  try {
    const noResponse = await authedClient.createAndPostOrder({
      tokenID: market.noToken,
      price: noBid,
      size: size,
      side: Side.BUY,
    }, {
      tickSize: tickSize,
      negRisk: negRisk,
    }, OrderType.GTC);

    console.log(`NO Order Response:`, noResponse);
    if (noResponse.orderID) {
      console.log(`✅ NO Order placed: ${noResponse.orderID}`);
    }
  } catch (e: any) {
    console.log(`❌ NO Error: ${e.message || e}`);
  }

  console.log("\n" + "=".repeat(60));
  console.log("DONE");
  console.log("=".repeat(60));
}

main().catch(console.error);
