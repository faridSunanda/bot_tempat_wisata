import asyncio
import aiomysql
import io
import random
import datetime
import qrcode
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# ================= CONFIG =================
BOT_TOKEN = "xxxx"
ADMIN_IDS = [xxxx]
QRIS_IMAGE_PATH = "assets/qris_static.jpeg"
TICKET_PRICE = 10000

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "db": "ngrembel_bot"
}
# ==========================================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --------- Helper fungsi koneksi DB ---------
async def get_conn():
    return await aiomysql.connect(**DB_CONFIG)

# --------- State Machine Pemesanan ----------
class OrderStates(StatesGroup):
    waiting_name = State()
    waiting_quantity = State()

# --------- Inisialisasi Database ----------
async def init_db():
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                tg_user_id BIGINT NOT NULL,
                tg_username VARCHAR(255),
                nama VARCHAR(255),
                jumlah INT,
                total INT,
                status VARCHAR(50),
                kode_tiket VARCHAR(255),
                created_at DATETIME
            )
        """)
    await conn.commit()
    conn.close()

# --------- Utility ---------
def generate_ticket_code():
    now = datetime.datetime.utcnow()
    return f"NGR-{now.year}-{random.randint(100000, 999999)}"

def make_ticket_qr_image(ticket_code: str) -> io.BytesIO:
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(ticket_code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    bio.name = "ticket.png"
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

# --------- Handlers ---------
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    text = (
        "<b>Halo! Selamat datang di tiket Wisata Ngrembel 🏞️</b>\n\n"
        "Untuk pesan tiket: /pesan_tiket\n"
        "Kalau sudah bayar: /sudah_bayar\n"
        "Untuk cek status tiket: /cek_tiket"
    )
    await message.reply(text, parse_mode="HTML")

# --- Proses Pemesanan Tiket ---
@dp.message_handler(commands=["pesan_tiket"])
async def cmd_pesan_tiket(message: types.Message):
    await OrderStates.waiting_name.set()
    await message.reply("Siapa nama lengkap untuk pemesanan tiket?")

@dp.message_handler(state=OrderStates.waiting_name, content_types=types.ContentTypes.TEXT)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(nama=message.text.strip())
    await OrderStates.next()
    await message.reply("Berapa jumlah tiket yang mau dipesan? (masukkan angka, contoh: 2)")

@dp.message_handler(state=OrderStates.waiting_quantity, content_types=types.ContentTypes.TEXT)
async def process_quantity(message: types.Message, state: FSMContext):
    jumlah_text = message.text.strip()
    if not jumlah_text.isdigit():
        await message.reply("Jumlah harus berupa angka. Coba lagi.")
        return
    jumlah = int(jumlah_text)
    if jumlah <= 0:
        await message.reply("Jumlah tiket minimal 1.")
        return

    data = await state.get_data()
    nama = data.get("nama")
    total = jumlah * TICKET_PRICE

    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO orders (tg_user_id, tg_username, nama, jumlah, total, status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (message.from_user.id, message.from_user.username or "", nama, jumlah, total, "pending", datetime.datetime.utcnow())
        )
        await conn.commit()
        order_id = cur.lastrowid
    conn.close()
    await state.finish()

    caption = (
        f"✅ Pesanan diterima!\n\n"
        f"Nama: {nama}\n"
        f"Jumlah tiket: {jumlah}\n"
        f"Total bayar: Rp {total:,}\n\n"
        "Silakan scan QRIS di atas untuk pembayaran.\n"
        "Setelah transfer, ketik /sudah_bayar\n\n"
        f"Order ID: #{order_id}"
    )
    try:
        await message.reply_photo(photo=InputFile(QRIS_IMAGE_PATH), caption=caption)
    except Exception:
        await message.reply(caption)

    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"New order #{order_id}\nNama: {nama}\nJumlah: {jumlah}\nTotal: Rp {total:,}\nUser: @{message.from_user.username or message.from_user.full_name}")
        except:
            pass

# --- User Sudah Bayar ---
@dp.message_handler(commands=["sudah_bayar"])
async def cmd_sudah_bayar(message: types.Message):
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, total, status FROM orders WHERE tg_user_id=%s ORDER BY id DESC LIMIT 1", (message.from_user.id,))
        row = await cur.fetchone()
    conn.close()

    if not row:
        await message.reply("Tidak menemukan order aktif. Coba /pesan_tiket dulu.")
        return

    order_id, total, status = row
    if status == "lunas":
        await message.reply("Pembayaran sudah diverifikasi. Cek tiket dengan /cek_tiket")
        return

    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("UPDATE orders SET status=%s WHERE id=%s", ("awaiting_validation", order_id))
        await conn.commit()
    conn.close()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Validasi", callback_data=f"validate:{order_id}"))
    kb.add(InlineKeyboardButton("❌ Tolak", callback_data=f"reject:{order_id}"))

    for admin in ADMIN_IDS:
        await bot.send_message(admin,
            f"User @{message.from_user.username or message.from_user.full_name} sudah bayar.\nOrder ID: #{order_id}\nTotal: Rp {total:,}",
            reply_markup=kb
        )

    await message.reply("Terima kasih! Admin akan memverifikasi pembayaran kamu.")

# --- Admin Lihat Pending Orders ---
@dp.message_handler(commands=["list_pending"])
async def cmd_list_pending(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Khusus admin.")
        return

    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, nama, jumlah, total, status FROM orders WHERE status IN ('pending','awaiting_validation') ORDER BY id DESC")
        rows = await cur.fetchall()
    conn.close()

    if not rows:
        await message.reply("Tidak ada order pending.")
        return

    text = "\n".join([f"#{r[0]} - {r[1]} - {r[2]} tiket - Rp {r[3]:,} - {r[4]}" for r in rows])
    await message.reply(text)

# --- Admin Lihat Semua Orders (Monitoring) ---
@dp.message_handler(commands=["list_order"])
async def cmd_list_order(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Khusus admin.")
        return

    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT id, nama, jumlah, total, status, created_at 
            FROM orders 
            ORDER BY id DESC 
            LIMIT 20
        """)
        rows = await cur.fetchall()
    conn.close()

    if not rows:
        await message.reply("Belum ada data order.")
        return

    text = "📋 <b>Daftar 20 Order Terbaru</b>\n\n"
    for r in rows:
        oid, nama, jumlah, total, status, created_at = r
        waktu = created_at.strftime("%d-%m-%Y %H:%M")
        text += f"#{oid} — {nama}\n🧾 {jumlah} tiket • Rp {total:,}\n📅 {waktu}\n📌 Status: <b>{status}</b>\n\n"

    await message.reply(text, parse_mode="HTML")


# --- Callback Validasi / Reject ---
@dp.callback_query_handler(lambda c: c.data and (c.data.startswith("validate:") or c.data.startswith("reject:")))
async def process_validation_callback(callback_query: types.CallbackQuery):
    admin_id = callback_query.from_user.id
    if admin_id not in ADMIN_IDS:
        await callback_query.answer("Khusus admin!", show_alert=True)
        return

    action, order_id = callback_query.data.split(":")
    order_id = int(order_id)

    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("SELECT tg_user_id, nama, jumlah, total FROM orders WHERE id=%s", (order_id,))
        row = await cur.fetchone()
    conn.close()

    if not row:
        await callback_query.answer("Order tidak ditemukan.", show_alert=True)
        return

    tg_user_id, nama, jumlah, total = row

    if action == "validate":
        kode_tiket = generate_ticket_code()
        conn = await get_conn()
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET status=%s, kode_tiket=%s WHERE id=%s", ("lunas", kode_tiket, order_id))
            await conn.commit()
        conn.close()

        bio = make_ticket_qr_image(kode_tiket)
        await bot.send_photo(tg_user_id, photo=InputFile(bio),
            caption=f"✅ Pembayaran diverifikasi!\n\nNama: {nama}\nJumlah: {jumlah}\nKode Tiket: {kode_tiket}")
        await callback_query.answer("Order divalidasi.")
    else:
        conn = await get_conn()
        async with conn.cursor() as cur:
            await cur.execute("UPDATE orders SET status=%s WHERE id=%s", ("rejected", order_id))
            await conn.commit()
        conn.close()

        await bot.send_message(tg_user_id, f"❌ Order #{order_id} ditolak oleh admin. Silakan cek kembali bukti pembayaran.")
        await callback_query.answer("Order ditolak.")

    await bot.edit_message_reply_markup(callback_query.message.chat.id, callback_query.message.message_id, reply_markup=None)

# --- Cek Tiket Sendiri ---
@dp.message_handler(commands=["cek_tiket"])
async def cmd_cek_tiket(message: types.Message):
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, jumlah, total, status, kode_tiket FROM orders WHERE tg_user_id=%s ORDER BY id DESC LIMIT 1", (message.from_user.id,))
        row = await cur.fetchone()
    conn.close()

    if not row:
        await message.reply("Belum ada pesanan ditemukan.")
        return

    oid, jumlah, total, status, kode_tiket = row
    text = f"Order #{oid}\nJumlah: {jumlah}\nTotal: Rp {total:,}\nStatus: {status}"
    if kode_tiket:
        text += f"\nKode Tiket: {kode_tiket}"
    await message.reply(text)

# --- Fallback Handler ---
@dp.message_handler()
async def fallback(message: types.Message):
    await message.reply("Ketik /pesan_tiket untuk mulai pesan tiket 🎫")

# --- Entrypoint ---
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    print("Bot running...")
    executor.start_polling(dp, skip_updates=True)
