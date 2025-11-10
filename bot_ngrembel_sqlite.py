# bot_ngrembel.py
import asyncio
import aiosqlite
import io
import random
import datetime
import qrcode
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ============= CONFIG =============
BOT_TOKEN = "7739470286:AAENQyesrOL6Bu7R1WMvfIFWiUlBOQ37O_k"
ADMIN_IDS = [5588770450]
QRIS_IMAGE_PATH = "assets/qris_static.jpeg"
TICKET_PRICE = 20000
DB_PATH = "ngrembel.db"
# ==================================

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# State machine untuk pemesanan
class OrderStates(StatesGroup):
    waiting_name = State()
    waiting_quantity = State()

# Utility: init database
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                tg_username TEXT,
                nama TEXT,
                jumlah INTEGER,
                total INTEGER,
                status TEXT,
                kode_tiket TEXT,
                created_at TEXT
            )
        """)
        await db.commit()

# Utility: create ticket code
def generate_ticket_code():
    now = datetime.datetime.utcnow()
    y = now.year
    rand = random.randint(100000, 999999)
    return f"NGR-{y}-{rand}"

# Utility: buat QR code gambar (ticket)
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

# ---------- Handlers ----------
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    text = (
        "Halo! Selamat datang di tiket Wisata *Ngrembel* \n\n"
        "Untuk pesan tiket ketik /pesan_tiket\n"
        "Kalau sudah scan QRIS dan bayar, ketik /sudah_bayar\n\n"
        "Untuk cek status tiket: /cek_tiket"
    )
    await message.reply(text, parse_mode="HTML")

# Start order
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
    text = message.text.strip()
    if not text.isdigit():
        await message.reply("Jumlah harus angka. Coba lagi.")
        return
    jumlah = int(text)
    if jumlah <= 0:
        await message.reply("Jumlah harus minimal 1.")
        return
    data = await state.get_data()
    nama = data.get("nama")
    total = jumlah * TICKET_PRICE

    # simpan ke DB
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO orders (tg_user_id, tg_username, nama, jumlah, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message.from_user.id, message.from_user.username or "", nama, jumlah, total, "pending", datetime.datetime.utcnow().isoformat())
        )
        await db.commit()
        order_id = cursor.lastrowid

    await state.finish()

    # Kirim ringkasan + QRIS statis
    caption = (
        f"Pesanan diterima!\n\n"
        f"Nama: {nama}\n"
        f"Jumlah tiket: {jumlah}\n"
        f"Total bayar: Rp {total:,}\n\n"
        "Silakan scan QRIS di atas untuk melakukan pembayaran.\n"
        "Setelah transfer, kembali ke chat ini dan ketik /sudah_bayar\n\n"
        f"Order ID: #{order_id}"
    )
    try:
        await message.reply_photo(photo=InputFile(QRIS_IMAGE_PATH), caption=caption)
    except Exception as e:
        # fallback kalau tidak bisa kirim image
        await message.reply(caption)

    # Notifikasi ke admin (opsional)
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"New order #{order_id}\nNama: {nama}\nJumlah: {jumlah}\nTotal: Rp {total:,}\nUser: @{message.from_user.username or message.from_user.full_name}\nGunakan /list_pending untuk cek.")
        except Exception:
            pass

# User menandakan sudah bayar
@dp.message_handler(commands=["sudah_bayar"])
async def cmd_sudah_bayar(message: types.Message):
    # Cari latest pending order user
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, total, status FROM orders WHERE tg_user_id = ? ORDER BY id DESC LIMIT 1", (message.from_user.id,))
        row = await cur.fetchone()
    if not row:
        await message.reply("Tidak menemukan order aktif. Pastikan kamu sudah memesan tiket dengan /pesan_tiket.")
        return
    order_id, total, status = row
    if status == "lunas":
        await message.reply("Pembayaran sudah terverifikasi sebelumnya. Cek tiketmu dengan /cek_tiket")
        return
    # update status menjadi awaiting_validation
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", ("awaiting_validation", order_id))
        await db.commit()

    # notify admin dengan tombol validasi
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(" Validasi", callback_data=f"validate:{order_id}"))
    kb.add(InlineKeyboardButton("Tolak", callback_data=f"reject:{order_id}"))

    admin_msg = (
        f"User @{message.from_user.username or message.from_user.full_name} menandakan sudah bayar.\n"
        f"Order ID: #{order_id}\nTotal: Rp {total:,}\n\n"
        "Silahkan cek pembayaran manual (rekening/QRIS merchant) lalu tekan tombol validasi atau tolak."
    )
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, admin_msg, reply_markup=kb)
        except Exception:
            pass

    await message.reply("Terima kasih — permintaan verifikasi sudah dikirim ke admin. Admin akan validasi manual.")

# Admin: lihat daftar pending / awaiting_validation
@dp.message_handler(commands=["list_pending"])
async def cmd_list_pending(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Perintah hanya untuk admin.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, nama, jumlah, total, status, tg_user_id FROM orders WHERE status IN ('pending','awaiting_validation') ORDER BY id DESC")
        rows = await cur.fetchall()
    if not rows:
        await message.reply("Tidak ada pending/awaiting orders.")
        return
    texts = []
    for r in rows:
        oid, nama, jumlah, total, status, tguid = r
        texts.append(f"#{oid} — {nama} — {jumlah} tiket — Rp {total:,} — {status} — user_id:{tguid}")
    out = "\n".join(texts)
    await message.reply(out)

# Admin validation callback handler
@dp.callback_query_handler(lambda c: c.data and (c.data.startswith("validate:") or c.data.startswith("reject:")))
async def process_validation_callback(callback_query: types.CallbackQuery):
    data = callback_query.data
    admin_id = callback_query.from_user.id
    if admin_id not in ADMIN_IDS:
        await callback_query.answer("Hanya admin yang bisa melakukan ini.", show_alert=True)
        return
    if data.startswith("validate:"):
        order_id = int(data.split(":")[1])
        # Ambil order
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT tg_user_id, nama, jumlah, total, status FROM orders WHERE id = ?", (order_id,))
            row = await cur.fetchone()
            if not row:
                await callback_query.answer("Order tidak ditemukan.", show_alert=True)
                return
            tg_user_id, nama, jumlah, total, status = row
            # Update ke lunas + buat kode tiket
            kode_tiket = generate_ticket_code()
            await db.execute("UPDATE orders SET status = ?, kode_tiket = ? WHERE id = ?", ("lunas", kode_tiket, order_id))
            await db.commit()
        # kirim tiket ke user
        ticket_text = (
            f"Pembayaran order #{order_id} diverifikasi \n\n"
            f"Nama: {nama}\n"
            f"Jumlah tiket: {jumlah}\n"
            f"Kode tiket: {kode_tiket}\n\n"
            "Tunjukkan kode/QR ini saat masuk ke Wisata Ngrembel."
        )
        # buat QR image untuk kode tiket
        bio = make_ticket_qr_image(kode_tiket)
        try:
            await bot.send_photo(tg_user_id, photo=InputFile(bio), caption=ticket_text)
        except Exception as e:
            # kalau gagal kirim image (user pernah block bot), notify admin saja
            await bot.send_message(admin_id, f"Gagal kirim tiket ke user {tg_user_id}. Mereka mungkin belum start bot atau block. Kode tiket: {kode_tiket}\nError: {e}")

        await callback_query.answer("Order telah divalidasi dan tiket dikirim.")
        await bot.edit_message_reply_markup(callback_query.message.chat.id, callback_query.message.message_id, reply_markup=None)

    elif data.startswith("reject:"):
        order_id = int(data.split(":")[1])
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT tg_user_id, nama FROM orders WHERE id = ?", (order_id,))
            row = await cur.fetchone()
            if not row:
                await callback_query.answer("Order tidak ditemukan.", show_alert=True)
                return
            tg_user_id, nama = row
            await db.execute("UPDATE orders SET status = ? WHERE id = ?", ("rejected", order_id))
            await db.commit()
        # notify user
        try:
            await bot.send_message(tg_user_id, f"Pembayaran untuk order #{order_id} *tidak ditemukan/ditolak* oleh admin. Silakan cek kembali bukti pembayaran atau hubungi admin.", parse_mode="Markdown")
        except Exception:
            pass
        await callback_query.answer("Order ditolak.")
        await bot.edit_message_reply_markup(callback_query.message.chat.id, callback_query.message.message_id, reply_markup=None)

# Cek tiket sendiri
@dp.message_handler(commands=["cek_tiket"])
async def cmd_cek_tiket(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, jumlah, total, status, kode_tiket FROM orders WHERE tg_user_id = ? ORDER BY id DESC LIMIT 1", (message.from_user.id,))
        row = await cur.fetchone()
    if not row:
        await message.reply("Belum ada pesanan ditemukan.")
        return
    oid, jumlah, total, status, kode_tiket = row
    text = f"Order #{oid}\nJumlah: {jumlah}\nTotal: Rp {total:,}\nStatus: {status}"
    if kode_tiket:
        text += f"\nKode tiket: {kode_tiket}"
    await message.reply(text)

# fallback: unknown command/message
@dp.message_handler()
async def echo_all(message: types.Message):
    await message.reply("Mau pesan tiket? Ketik /pesan_tiket")

# Entrypoint
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    print("Bot running...")
    executor.start_polling(dp, skip_updates=True)
