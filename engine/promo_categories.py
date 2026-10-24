from __future__ import annotations

"""Deterministic Indonesian retail-promotion category taxonomy and classifier.

The classifier prioritizes the product/SKU itself over campaign wording. General
campaign headings are separated from genuinely unknown products so that the
"Promo Umum / Multi-Kategori" label is not used as a catch-all.
"""

import re
import unicodedata

# The ordered taxonomy is also used by the Excel reference sheet.
CATEGORY_TAXONOMY = [
    ("Makanan & Bahan Pokok", "Beras, minyak goreng, gula, tepung, telur, bumbu, saus"),
    ("Minuman & Produk Susu", "Air mineral, kopi, teh, susu, yoghurt, keju, jus"),
    ("Snack & Makanan Kemasan", "Makanan kemasan, snack, mi instan, biskuit, roti kemasan, dan makanan olahan"),
    ("Restoran & Makanan Siap Saji", "Menu dan paket restoran/kafe, makanan siap saji, serta minuman yang dijual sebagai menu outlet"),
    ("Produk Segar & Beku", "Buah, sayur, daging, ikan, seafood, makanan beku"),
    ("Perawatan Tubuh & Mandi", "Sabun mandi, body wash, deodorant, body lotion"),
    ("Perawatan Rambut & Kulit", "Sampo, kondisioner, skincare, facial wash, sunscreen"),
    ("Kecantikan & Kosmetik", "Makeup, parfum, kosmetik, produk kecantikan"),
    ("Perawatan Gigi & Mulut", "Pasta gigi, sikat gigi, obat kumur, benang gigi"),
    ("Cuci Pakaian", "Deterjen, pelembut, pewangi dan pemutih pakaian"),
    ("Kebersihan Rumah Tangga", "Pembersih toilet/rumah, sabun cuci piring, pengharum ruangan, anti-serangga"),
    ("Tisu & Produk Kertas", "Tisu wajah, tisu toilet, tisu dapur, kertas rumah tangga"),
    ("Bayi & Anak", "Popok, tisu bayi, susu formula, perawatan bayi dan anak"),
    ("Kesehatan & Farmasi", "Vitamin, alat kesehatan, P3K, obat bebas"),
    ("Rumah Tangga & Dapur", "Alat masak, wadah, perabot, perlengkapan dapur"),
    ("Hewan Peliharaan", "Makanan kucing/anjing, pasir kucing, perawatan hewan"),
    ("Elektronik & Gadget", "Smartphone, laptop, televisi, aksesori dan perangkat elektronik"),
    ("Fashion & Aksesori", "Pakaian, sepatu, tas, jam tangan, aksesori"),
    ("Otomotif", "Oli, aksesori kendaraan, suku cadang, perawatan kendaraan"),
    ("Pendidikan, Buku & Alat Tulis", "Buku, alat tulis, perlengkapan sekolah"),
    ("Voucher & Gift Card", "Voucher belanja, voucher restoran, kupon dan gift card"),
    ("Pembayaran, Cashback & Loyalitas", "Cashback bank, kartu kredit, QRIS, poin dan reward"),
    ("Belanja Online & Marketplace", "Marketplace, voucher ongkir, gratis ongkir, promo platform belanja"),
    ("Perjalanan & Transportasi", "Tiket pesawat/kereta, hotel, transportasi daring"),
    ("Hiburan & Rekreasi", "Bioskop, taman rekreasi, tiket acara, permainan"),
    ("Telekomunikasi & Digital", "Pulsa, paket data, internet dan layanan digital"),
    ("Layanan & Jasa", "Servis, langganan, jasa rumah tangga dan layanan profesional"),
    ("Lainnya / Perlu Review", "Produk tidak cukup jelas untuk dikategorikan dengan yakin"),
    ("Promo Umum / Multi-Kategori", "Rangkuman beberapa promo, promo multi-merchant, kampanye umum"),
]

CATEGORY_NAMES = [category for category, _ in CATEGORY_TAXONOMY]
GENERAL = "Promo Umum / Multi-Kategori"
REVIEW = "Lainnya / Perlu Review"

# Product-identity rules are evaluated before campaign context. Most terms are
# deliberately Indonesian/English retail phrases found in the source pages.
RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Bayi & Anak", "Popok", (r"\bpopok\b", r"\bpampers\b", r"\bpants bayi\b", r"\bdiapers?\b", r"\btisu bayi\b", r"\bsusu formula\b", r"\bbaby wipes?\b")),
    ("Perawatan Gigi & Mulut", "Pasta/sikat gigi", (r"\bpasta gigi\b", r"\bsikat gigi\b", r"\bobat kumur\b", r"\bdental floss\b", r"\bbenang gigi\b", r"\btoothpaste\b", r"\btoothbrush\b", r"\bmouthwash\b", r"\bcolgate\b", r"\bpepsodent\b", r"\bsensodyne\b", r"\boral b\b")),
    ("Cuci Pakaian", "Deterjen", (r"\bdeterjen\b", r"\bdetergen\b", r"\bdetergent\b", r"\bdetergent cair\b", r"\bpelembut pakaian\b", r"\bpewangi pakaian\b", r"\bpemutih pakaian\b", r"\bsoftener pakaian\b", r"\bfabric softener\b", r"\bdowny\b", r"\brinso\b", r"\bmolto\b", r"\bso klin\b")),
    ("Kebersihan Rumah Tangga", "Pembersih rumah/dapur", (r"\bsabun cuci piring\b", r"\bdishwash(?:ing)?\b", r"\bpembersih lantai\b", r"\bpembersih kamar mandi\b", r"\bkarbol\b", r"\bpembersih kaca\b", r"\bpengharum ruangan\b", r"\bdisinfectant\b", r"\bdisinfektan\b", r"\bsunlight\b", r"\bwipol\b", r"\bporstex\b")),
    ("Tisu & Produk Kertas", "Tisu/kertas", (r"\btisu\b", r"\btissue\b", r"\bwet wipes\b", r"\btisu basah\b", r"\btoilet paper\b", r"\bpaper towel\b", r"\bpaper napkin\b", r"\bhanduk kertas\b")),
    ("Hewan Peliharaan", "Makanan/perawatan hewan", (r"\bmakanan kucing\b", r"\bmakanan anjing\b", r"\bpasir kucing\b", r"\bcat litter\b", r"\bpet food\b", r"\bcat food\b", r"\bdog food\b", r"\bpet shampoo\b")),
    ("Otomotif", "Oli/aksesori kendaraan", (r"\boli mesin\b", r"\boli motor\b", r"\boli mobil\b", r"\bsuku cadang\b", r"\bspare ?part\b", r"\baksesori mobil\b", r"\baksesori motor\b", r"\bhelm(?:et)?\b", r"\bban motor\b", r"\bcar care\b")),
    ("Elektronik & Gadget", "Perangkat/aksesori elektronik", (r"\bsmartphone\b", r"\biphone\b", r"\bipad\b", r"\bsmartwatch\b", r"\bhandphone\b", r"\bponsel\b", r"\blaptop\b", r"\bnotebook\b", r"\btelevisi\b", r"\btv led\b", r"\bmonitor\b", r"\bprinter\b", r"\bheadphone\b", r"\bearphone\b", r"\bearbuds\b", r"\bcharger\b", r"\bkabel usb\b", r"\bpower ?bank\b", r"\bkeyboard\b", r"\bmouse wireless\b", r"\bspeaker bluetooth\b", r"\bkamera digital\b", r"\bssd\b", r"\bflashdisk\b", r"\bflash drive\b")),
    ("Pendidikan, Buku & Alat Tulis", "Buku/alat tulis", (r"\bbuku tulis\b", r"\bnovel\b", r"\bbuku pelajaran\b", r"\bkamus\b", r"\bpena\b", r"\bpulpen\b", r"\bpensil\b", r"\bspidol\b", r"\balat tulis\b", r"\bstationery\b", r"\bback to school\b")),
    ("Fashion & Aksesori", "Pakaian/sepatu/tas", (r"\bkaos\b", r"\bkemeja\b", r"\bdress\b", r"\bcelana jeans\b", r"\bjaket\b", r"\bsepatu\b", r"\bshoes?\b", r"\bsneakers?\b", r"\bsandal\b", r"\bnike\b", r"\badidas\b", r"\bpuma\b", r"\buniqlo\b", r"\bzara\b", r"\bh and m\b", r"\btas wanita\b", r"\btas ransel\b", r"\bhandbag\b", r"\bjam tangan\b", r"\bdompet\b", r"\bscarf\b")),
    ("Kesehatan & Farmasi", "Vitamin/alat kesehatan/obat bebas", (r"\bvitamin\b", r"\bsuplemen\b", r"\bmultivitamin\b", r"\bobat bebas\b", r"\bplester luka\b", r"\bp3k\b", r"\btermometer\b", r"\btensimeter\b", r"\bmasker medis\b", r"\bantiseptik\b", r"\bminyak kayu putih\b", r"\bminyak angin\b", r"\bparacetamol\b", r"\bparasetamol\b")),
    ("Rumah Tangga & Dapur", "Peralatan rumah/dapur", (r"\bpanci\b", r"\bwajan\b", r"\bpisau dapur\b", r"\bspatula\b", r"\bperalatan dapur\b", r"\bwadah makanan\b", r"\bcontainer makanan\b", r"\bkotak makan\b", r"\bbotol minum\b", r"\btermos\b", r"\bperabot\b", r"\bkeranjang pakaian\b", r"\bsapu\b", r"\bpel lantai\b", r"\bkeset\b")),
    ("Perawatan Tubuh & Mandi", "Sabun mandi/deodorant/body lotion", (r"\bsabun mandi\b", r"\bbody wash\b", r"\bshower gel\b", r"\bbody lotion\b", r"\bbody cologne\b", r"\bdeodorant\b", r"\bdeodoran\b", r"\bantiperspirant\b", r"\bbody scrub\b", r"\bhand cream\b", r"\bhand sanitizer\b", r"\bhandwash\b", r"\bsabun cair\b", r"\blifebuoy\b", r"\bdetol body\b", r"\bbiore body\b")),
    ("Perawatan Rambut & Kulit", "Sampo/skincare/perawatan kulit", (r"\bsampo\b", r"\bshampoo\b", r"\bkondisioner\b", r"\bconditioner\b", r"\bhair serum\b", r"\bhair mask\b", r"\btonik rambut\b", r"\bfacial wash\b", r"\bface wash\b", r"\bfacial cleanser\b", r"\bcleanser wajah\b", r"\bskincare\b", r"\bskin care\b", r"\bsunscreen\b", r"\bsunblock\b", r"\bmoisturizer\b", r"\bpelembap wajah\b", r"\btoner wajah\b", r"\bserum wajah\b", r"\bretinol\b", r"\bwardah lightening\b", r"\bponds facial\b", r"\bpond.s facial\b")),
    ("Kecantikan & Kosmetik", "Makeup/parfum/kosmetik", (r"\blipstick\b", r"\blip tint\b", r"\blip cream\b", r"\bfoundation\b", r"\bbedak\b", r"\bblush ?on\b", r"\beyeliner\b", r"\bmascara\b", r"\bmake ?up\b", r"\bkosmetik\b", r"\bparfum\b", r"\beau de parfum\b", r"\bfragrance\b", r"\bcolonge\b", r"\bbody mist\b", r"\bnail polish\b", r"\bkuku palsu\b")),
    ("Voucher & Gift Card", "Voucher/kupon/gift card", (r"\bgift card\b", r"\bgiftcard\b", r"\bvoucher belanja\b", r"\bvoucher restoran\b", r"\bkupon belanja\b", r"\bgift voucher\b", r"\bvoucher rp\b")),
    ("Pembayaran, Cashback & Loyalitas", "Cashback/bank/poin/reward", (r"\bcashback\b", r"\bcash back\b", r"\bqris\b", r"\bkartu kredit\b", r"\bkartu debit\b", r"\bkartu bank\b", r"\bpromo bank\b", r"\bpaylater\b", r"\bcicilan\b", r"\bpoin reward\b", r"\breward points?\b", r"\bloyalty points?\b", r"\bmybca\b", r"\bmandiri\b.*\bdiskon\b", r"\bbca\b.*\bdiskon\b", r"\bbri\b.*\bcashback\b", r"\byup\b.*\bcashback\b")),
    ("Belanja Online & Marketplace", "Marketplace/ongkir/platform belanja", (r"\bgratis ongkir\b", r"\bvoucher ongkir\b", r"\bongkos kirim\b", r"\bmarketplace\b", r"\bshopee\b", r"\btokopedia\b", r"\bblibli\b", r"\blazada\b", r"\btiktok shop\b", r"\bzalora\b", r"\bflash sale online\b", r"\bcheckout murah\b")),
    ("Perjalanan & Transportasi", "Tiket/hotel/transportasi", (r"\btiket pesawat\b", r"\btiket kereta\b", r"\bhotel\b", r"\btraveloka\b", r"\btiket\.com\b", r"\bbooking\.com\b", r"\bairlines?\b", r"\btransportasi daring\b", r"\bgojek\b", r"\bgrabcar\b", r"\bgrab bike\b", r"\bgrabfood ride\b", r"\bsewa mobil\b", r"\brental mobil\b")),
    ("Hiburan & Rekreasi", "Bioskop/taman/tiket acara", (r"\bbioskop\b", r"\bcinema\b", r"\bxxi\b", r"\bcgv\b", r"\btaman rekreasi\b", r"\btheme park\b", r"\bwaterpark\b", r"\bkonser\b", r"\btiket acara\b", r"\bplayground\b", r"\btimezone\b", r"\bgame center\b", r"\bkaraoke\b")),
    ("Telekomunikasi & Digital", "Pulsa/paket data/internet", (r"\bpulsa\b", r"\bpaket data\b", r"\bkuota internet\b", r"\bpaket internet\b", r"\bprovider internet\b", r"\bindihome\b", r"\bindosat\b", r"\btelkomsel\b", r"\bxl axiata\b", r"\bby\.u\b", r"\bsmartfren\b", r"\btri indonesia\b", r"\bsubscription digital\b", r"\blangganan streaming\b", r"\bnetflix\b", r"\bspotify\b", r"\bcloud storage\b")),
    ("Layanan & Jasa", "Servis/langganan/jasa", (r"\bjasa\b", r"\bservice center\b", r"\bservis kendaraan\b", r"\bcuci sepatu\b", r"\blaundry kiloan\b", r"\bbersih rumah\b", r"\bperawatan rumah\b", r"\basuransi\b", r"\bjasa keuangan\b", r"\bsalon\b", r"\bbarbershop\b", r"\bmembership\b", r"\bbiaya administrasi\b")),
]

# Clear SKU-like food and retail terms; product text wins over campaign-level offers.
FOOD_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Restoran & Makanan Siap Saji", "Menu/paket restoran", (r"\bmenu\b", r"\bpaket makanan\b", r"\bpaket nasi\b", r"\bpaket hemat\b", r"\bset menu\b", r"\bcombo meal\b", r"\bbento\b", r"\bcajun fries\b", r"\bgrilled chicken\b", r"\bcrispy chicken\b", r"\bchicken wings\b", r"\bchicken tenders\b", r"\bbulgogi\b", r"\bchillz\b", r"\bmilo float\b", r"\bburger\b", r"\bsandwich\b", r"\bsushi\b", r"\bramen\b", r"\budon\b", r"\bpizza\b", r"\bpasta\b", r"\bayam goreng\b", r"\bfried chicken\b", r"\brice bowl\b", r"\bayam crispy\b", r"\bkentang goreng\b", r"\bhotdog\b", r"\bsteak\b", r"\bdimsum\b", r"\bdim sum\b", r"\bbakmi\b", r"\bmie ayam\b", r"\bbakso\b", r"\bsiomay\b", r"\bsoto\b", r"\bcroissant\b", r"\bdonat\b", r"\bdonut\b", r"\broti isi\b", r"\bmochi\b", r"\bparis baguette\b", r"\bpopeyes\b", r"\brotio\b", r"\broti'o\b", r"\bkopi kenangan\b", r"\bstarbucks\b", r"\bhokben\b", r"\bkfc\b", r"\bmcdonald\b", r"\bmcd\b", r"\bsubway\b", r"\bwingstop\b", r"\bbakmi gm\b", r"\bj\.co\b", r"\bgokana\b", r"\bmarugame\b", r"\byoshinoya\b", r"\bpizza hut\b", r"\bphd\b", r"\bfore coffee\b", r"\bjanji jiwa\b", r"\britchee?se\b", r"\bgenki sushi\b", r"\bchatime\b", r"\bmixue\b", r"\bburger bangor\b", r"\bpoint coffee\b", r"\bsolaria\b", r"\bes teler 77\b", r"\ba&w\b")),
    ("Makanan & Bahan Pokok", "Bahan pokok/bumbu", (r"\beras\b", r"\bminyak goreng\b", r"\bcooking oil\b", r"\bgula pasir\b", r"\bgula aren\b", r"\btepung\b", r"\btelur\b", r"\bbumbu\b", r"\bgaram\b", r"\bkecap\b", r"\bsaus sambal\b", r"\bsaus tomat\b", r"\bmargarin\b", r"\bmentega\b", r"\bsantan\b", r"\bkaldu\b", r"\bmi instan bumbu\b", r"\bspice mix\b")),
    ("Minuman & Produk Susu", "Minuman/susu/kopi/teh", (r"\bair mineral\b", r"\bair putih\b", r"\bsusu\b", r"\byogh?urt\b", r"\bkeju\b", r"\bkrimer\b", r"\bkopi\b", r"\bcoffee\b", r"\bteh\b", r"\bjus\b", r"\bjuice\b", r"\bsoda\b", r"\bminuman\b", r"\bsirup\b", r"\benergi drink\b", r"\bisotonik\b", r"\bsoft drink\b", r"\bcola\b", r"\bminuman serbuk\b", r"\bminuman cokelat\b", r"\buht\b", r"\bfull cream\b")),
    ("Produk Segar & Beku", "Buah/sayur/daging/beku", (r"^(?:buah\s+)?(?:jeruk|apel|alpukat|pisang|anggur|semangka|pepaya|melon|wortel)(?:\s+(?:medan|mandarin|malang|segar|lokal|impor|import))?(?:\s+\d.*)?$", r"^sayur(?:an)?\b", r"\bdaging segar\b", r"\bikan segar\b", r"\bseafood segar\b", r"\bfrozen food\b", r"\bmakanan beku\b", r"\bnugget\b", r"\bsosis beku\b", r"\bes krim\b", r"\bice cream\b")),
    ("Snack & Makanan Kemasan", "Snack/mi/biskuit/makanan kemasan", (r"\bmi instan\b", r"\bmie instan\b", r"\bindomie\b", r"\bmie sedaap\b", r"\bkeripik\b", r"\bchips\b", r"\bsnack\b", r"\bbiskuit\b", r"\bbiscuit\b", r"\bcookies\b", r"\bwafer\b", r"\bcokelat\b", r"\bchocolate\b", r"\bpermen\b", r"\bcandy\b", r"\bpopcorn\b", r"\bkacang\b", r"\broti tawar\b", r"\broti kemasan\b", r"\bmakanan kaleng\b", r"\babon\b", r"\bgranola bar\b", r"\bcereal bar\b", r"\bpop mie\b", r"\bmalkist\b")),
]

GENERAL_CAMPAIGN_PATTERNS = (
    r"^(?:cek\s+(?:info\s+)?promo\b|info\s+promo\b)",
    r"^katalog(?:\s+promo)?\b",
    r"^promo\s+(?:jsm\b|katalog\b|serba gratis\b|berhadiah\b|terbaru\b|minggu ini\b|bulan ini\b|hari ini\b|mingguan\b|bulanan\b|weekend\b|minggu kedua\b)",
    r"^(?:rangkuman|kumpulan|pilihan)\s+(?:berbagai\s+)?promo\b",
    r"^\d+\s+promo\b",
    r"\b(?:beberapa|berbagai|multi[- ]merchant|multi[- ]kategori)\s+promo\b",
    r"\bpromo\s+(?:dari|di)\s+(?:berbagai|sejumlah)\s+merchant\b",
)



# Fine-grained labels make the second Excel column useful for filtering.
SUBCATEGORY_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    # Specific names that often appear abbreviated in Indonesian retailer catalogues.
    ("Hewan Peliharaan", "Makanan hewan", (r"\bkitten\b", r"\bdoraemon kitten\b")),
    ("Otomotif", "Pengharum/perawatan mobil", (r"\bairpro\b", r"\bcar parfume?\b", r"\bcar perfume\b", r"\bcar fragrance\b")),
    ("Elektronik & Gadget", "Baterai/perangkat listrik", (r"\bbattery\b", r"\bbaterai\b", r"\blr-6\b", r"\balakaline aa\b")),
    ("Makanan & Bahan Pokok", "Saus/bumbu/pasta masak", (r"\bla fonte\b", r"\bblgns\b", r"\bpronas\b", r"\bbolognese\b", r"\bsasa bmb\b", r"\bsajiku bmb\b", r"\bbmb\b", r"\bdua belibis\b", r"\bs cabe\b", r"\bsaus pasta\b")),
    ("Minuman & Produk Susu", "Kopi/teh/minuman kemasan", (r"\bkapal api\b", r"\binstblk\b", r"\bsosro\b", r"\btehbotol\b", r"\bteh botol\b", r"\bkino larutan\b", r"\blencir prebiotic drink\b", r"\blencir\b", r"\bair minum\b", r"\boksigen sporty\b", r"\bph tinggi\b", r"\balkaline water\b")),
    ("Perawatan Tubuh & Mandi", "Body lotion/serum", (r"\bvaseline\b", r"\bscarlett\b", r"\bb srm\b", r"\bbody lotion\b", r"\bbody serum\b")),
    ("Perawatan Tubuh & Mandi", "Deodorant", (r"\brexona\b", r"\bnivea\b.*\bro\b", r"\bdeodorant\b", r"\bdeodoran\b", r"\bantiperspirant\b")),
    ("Perawatan Tubuh & Mandi", "Sabun/perawatan tubuh", (r"\bnuvo sabun kesehatan cair\b", r"\bnuvo\b", r"\bgiv bw\b", r"\bzen anti bacterial body soap\b", r"\bbody soap\b", r"\bbody shower\b", r"\bbiore serum body foam\b", r"\bposh body spray\b", r"\bantis pembersih tangan\b", r"\bhand soap\b", r"\bkapas bulat\b", r"\bkapas\b")),
    ("Perawatan Tubuh & Mandi", "Kebersihan wanita", (r"\blaurier\b", r"\bpembalut\b", r"\bcharm\b", r"\bsoftex\b", r"\bkotex\b", r"\bsafe night\b")),
    ("Perawatan Rambut & Kulit", "Sampo/perawatan rambut", (r"\bclear shp\b", r"\bnr hair tonic\b", r"\bhair tonic\b")),
    ("Perawatan Rambut & Kulit", "Pembersih wajah/skincare", (r"\bkao biore pore\b", r"\bbiore facial foam\b", r"\bbiore facial\b", r"\bmen s cleansing black\b", r"\bgarnier micellar\b")),
    ("Kecantikan & Kosmetik", "Parfum/body spray", (r"\bvitalis\b", r"\beau de parfum\b", r"\bposh body spray\b")),
    ("Kesehatan & Farmasi", "Obat/perawatan medis", (r"\bcounterpain\b", r"\bbiotrue\b", r"\bbiotrue multipurpose solution\b", r"\bika alkohol\b", r"\balkohol 70%\b", r"\bfreshcare\b", r"\bimboost\b", r"\bom3heart\b", r"\brohto\b", r"\bobat mata\b", r"\bplester\b")),
    ("Produk Segar & Beku", "Es krim/dessert beku", (r"\bjoyday\b", r"\bwall s\b", r"\bwalls\b", r"\bmagnum\b", r"\bcornetto\b", r"\baice\b", r"\bglico\b", r"\bpaddle pop\b", r"\bmonaka\b", r"\bhisteria\b", r"\bfeast\b", r"\bice cream\b", r"\bes krim\b", r"\bcream cup\b")),
    ("Produk Segar & Beku", "Daging olahan/beku", (r"\bchamp chicken sausage\b", r"\bchicken sausage\b", r"\bsosis ayam\b", r"\bsausage\b")),
    ("Snack & Makanan Kemasan", "Pasta/cereal/snack kemasan", (r"\bwow spageti\b", r"\bspageti\b", r"\bspaghetti\b", r"\bparoti\b", r"\bbagelen\b", r"\bchuba\b", r"\bcassava\b", r"\bpotabee\b", r"\bchilli? beef\b", r"\bkellogg\b", r"\bfroot loops\b", r"\bcoco fills\b", r"\bselection round kapas\b", r"\bmom s recipe\b", r"\bsilky pud\b", r"\bpudding\b", r"\bpuding\b", r"\bmini bites\b", r"\bcheese cake\b")),
    ("Kebersihan Rumah Tangga", "Perlengkapan rumah tangga", (r"\bsarung tangan plastik\b", r"\bbagus sarung tangan\b")),
    # Common shorthand and high-frequency Indonesian FMCG brands found in catalog feeds.
    ("Bayi & Anak", "Mainan/perlengkapan anak", (r"\bhot wheels\b", r"\btoy(?:s)?\b", r"\bmainan anak\b")),
    ("Bayi & Anak", "Perlengkapan/susu/perawatan bayi", (r"\bbaby\b", r"\bmamy poko\b", r"\bmamypoko\b", r"\bhappy nappy\b", r"\bsgm\b", r"\blactogrow\b", r"\blactogen\b", r"\benfagrow\b", r"\bbebelac\b", r"\bnutrilon\b", r"\bkodomo\b", r"\bsun bubur\b", r"\bcotton bud baby\b", r"\bmitu b wipes\b", r"\bmitu\b")),
    ("Otomotif", "Pengharum/perawatan mobil", (r"\bairpro\b", r"\bcar parfume?\b", r"\bcar perfume\b", r"\bcar fragrance\b")),
    ("Hewan Peliharaan", "Makanan hewan", (r"\bcat choize\b", r"\bchoize\b", r"\bwhiskas\b", r"\bmeo\b", r"\bdog food\b", r"\bcat food\b")),
    ("Kebersihan Rumah Tangga", "Pembersih toilet/kloset", (r"\bpembersih porselen\b", r"\bporcelain cleaner\b", r"\bharpic\b", r"\bvixal\b", r"\bpembersih toilet\b", r"\btoilet cleaner\b", r"\bpembersih kloset\b", r"\btoilet colour ball\b")),
    ("Kebersihan Rumah Tangga", "Anti nyamuk/serangga", (r"\binsektisida\b", r"\binsecticide\b", r"\bbaygon\b", r"\bhit insektisida\b", r"\banti nyamuk\b", r"\bobat nyamuk\b", r"\bvape elect mat\b", r"\bvape aerosol\b", r"\bvape liquid\b", r"\banti serangga\b", r"\bmosquito repellent\b")),
    ("Kebersihan Rumah Tangga", "Sabun cuci piring", (r"\bmama lemon\b", r"\bmama lime\b", r"\bpencuci piring\b", r"\bliquid dishwash\b")),
    ("Cuci Pakaian", "Pelicin pakaian", (r"\brapika\b", r"\bpelicin pakaian\b", r"\bstarch spray\b", r"\bkanji pakaian\b", r"\biron spray\b")),
    ("Cuci Pakaian", "Penghilang noda pakaian", (r"\bvanish\b", r"\bpenghilang noda\b", r"\bstain remover\b")),
    ("Perawatan Tubuh & Mandi", "Kebersihan wanita", (r"\bpembalut\b", r"\bcharm\b", r"\bsoftex\b", r"\bkotex\b", r"\bsafe night\b", r"\bwing 29cm\b")),
    ("Perawatan Gigi & Mulut", "Obat kumur/perawatan mulut", (r"\blisterine\b", r"\bmw cool mint\b", r"\bmw pro zero\b")),
    ("Perawatan Rambut & Kulit", "Sampo", (r"\bclear shp\b", r"\bclear shampoo\b")),
    ("Perawatan Rambut & Kulit", "Pembersih wajah/skincare", (r"\bderma angel\b", r"\bpond.?s\b", r"\bgarnier micellar\b", r"\bmicellar cleansing water\b", r"\bacne clay stick\b", r"\bacne\b")),
    ("Kecantikan & Kosmetik", "Pewarna rambut/kosmetik lain", (r"\bcultusia\b", r"\bhair color\b", r"\bhair dye\b", r"\bbleaching rambut\b")),
    ("Perawatan Tubuh & Mandi", "Deodorant/body serum", (r"\bnivea\b.*\bro\b", r"\bscarlett b srm\b", r"\bbody serum\b", r"\bkahf\b")),
    ("Kesehatan & Farmasi", "Pereda demam/perawatan kesehatan", (r"\bkoolfever\b", r"\bplester demam\b", r"\bkondom\b", r"\bcondom\b", r"\bsutra kondom\b", r"\bnutrive benecol\b", r"\bbenecol\b", r"\bconfidence adult\b")),
    ("Produk Segar & Beku", "Es krim/dessert beku", (r"\bwall s\b", r"\bwalls\b", r"\bmagnum\b", r"\bcornetto\b", r"\baice\b", r"\bglico\b", r"\bpaddle pop\b", r"\bmonaka\b", r"\bhisteria\b", r"\bfeast\b", r"\bice cream\b", r"\bes krim\b", r"\bcream cup\b")),
    ("Makanan & Bahan Pokok", "Mayones/saus/sambal/bumbu", (r"\bmayo\b", r"\bmayonnaise\b", r"\bkewpie\b", r"\bmaestro mayo\b", r"\bsambal\b", r"\bsbl\b", r"\bdelmonte\b", r"\bleekumkee\b", r"\blee kum kee\b", r"\bblue band\b", r"\bminyak wijen\b", r"\bsesame oil\b", r"\btep terigu\b", r"\bbwn kukus\b", r"\bnutricake\b", r"\bsasa sbl\b", r"\bindomie? food sbl\b")),
    ("Snack & Makanan Kemasan", "Cokelat/permen/snack kemasan", (r"\bcadbury\b", r"\bsilver ?queen\b", r"\bmentos\b", r"\byupi\b", r"\bpringles\b", r"\bsoyjoy\b", r"\bkuaci\b", r"\boat bar\b", r"\bcereal cup\b", r"\bkoko krunch\b", r"\bspaghetti instant\b", r"\bmie grg\b", r"\bmie goreng\b", r"\bsedaap mie grg\b", r"\bnata de coco\b", r"\bjelly\b", r"\bchoco\b", r"\bchocolate\b", r"\bagar\b", r"\bwafer\b", r"\bbrownies\b", r"\bcake mix\b")),
    ("Minuman & Produk Susu", "Air mineral/minuman herbal", (r"\bvio\b", r"\bpristine\b", r"\bcleo\b", r"\ble minerale\b", r"\bair mnrl\b", r"\bad(e|em) m sari\b", r"\badem sari\b", r"\bsidomuncul\b", r"\bkunyit asam\b", r"\blarutan penyegar\b", r"\bcap kaki tiga\b", r"\boatside\b", r"\boat milk\b", r"\bmilk barista\b")),
    ("Makanan & Bahan Pokok", "Makanan kaleng/saus ikan", (r"\bsardines?\b", r"\btuna\b", r"\bikan kaleng\b", r"\basahi sardines\b", r"\bmaya tuna\b", r"\bcan 155 g\b")),
    ("Bayi & Anak", "Popok", (r"\bpopok\b", r"\bpampers\b", r"\bdiapers?\b", r"\bpants bayi\b")),
    ("Bayi & Anak", "Tisu bayi", (r"\btisu bayi\b", r"\bbaby wipes?\b")),
    ("Bayi & Anak", "Susu formula", (r"\bsusu formula\b", r"\binfant formula\b")),
    ("Bayi & Anak", "Perawatan bayi", (r"\bbaby shampoo\b", r"\bbaby lotion\b", r"\bperawatan bayi\b", r"\bmy baby\b")),
    ("Perawatan Gigi & Mulut", "Pasta gigi", (r"\bpasta gigi\b", r"\btoothpaste\b", r"\bcolgate\b", r"\bpepsodent\b", r"\bsensodyne\b")),
    ("Perawatan Gigi & Mulut", "Sikat gigi", (r"\bsikat gigi\b", r"\btoothbrush\b", r"\boral b\b")),
    ("Perawatan Gigi & Mulut", "Obat kumur/perawatan mulut", (r"\bobat kumur\b", r"\bmouthwash\b", r"\bdental floss\b", r"\bbenang gigi\b")),
    ("Cuci Pakaian", "Deterjen", (r"\bdeterjen\b", r"\bdetergen\b", r"\bdetergent\b", r"\bsoftergent\b", r"\bliq det\b", r"\bliquid det\b", r"\bgentle gen liq\b", r"\bjaz1 det\b", r"\bdet pesona\b", r"\bdet powder\b", r"\brinso\b", r"\bso klin\b")),
    ("Cuci Pakaian", "Pelembut/pewangi pakaian", (r"\bpelembut pakaian\b", r"\bpewangi pakaian\b", r"\bfabric softener\b", r"\bsoftener pakaian\b", r"\bdowny\b", r"\bmolto\b")),
    ("Cuci Pakaian", "Pemutih pakaian", (r"\bpemutih pakaian\b", r"\bbleach pakaian\b")),
    ("Kebersihan Rumah Tangga", "Sabun cuci piring", (r"\bsabun cuci piring\b", r"\bdishwash(?:ing)?\b", r"\bsunlight\b")),
    ("Kebersihan Rumah Tangga", "Pembersih lantai/kamar mandi", (r"\bpembersih lantai\b", r"\bpembersih kamar mandi\b", r"\bkarbol\b", r"\bwipol\b", r"\bporstex\b")),
    ("Kebersihan Rumah Tangga", "Pembersih rumah lainnya", (r"\bpembersih kaca\b", r"\bdisinfectant\b", r"\bdisinfektan\b")),
    ("Kebersihan Rumah Tangga", "Pengharum ruangan", (r"\bpengharum ruangan\b", r"\broom fragrance\b", r"\bair freshener\b")),
    ("Tisu & Produk Kertas", "Tisu toilet", (r"\btisu toilet\b", r"\btoilet paper\b")),
    ("Tisu & Produk Kertas", "Tisu dapur", (r"\btisu dapur\b", r"\bpaper towel\b", r"\bhanduk kertas\b")),
    ("Tisu & Produk Kertas", "Tisu wajah", (r"\btisu wajah\b", r"\btisu\b", r"\btissue\b")),
    ("Hewan Peliharaan", "Makanan hewan", (r"\bmakanan kucing\b", r"\bmakanan anjing\b", r"\bpet food\b", r"\bcat food\b", r"\bdog food\b")),
    ("Hewan Peliharaan", "Pasir kucing", (r"\bpasir kucing\b", r"\bcat litter\b")),
    ("Hewan Peliharaan", "Perawatan hewan", (r"\bpet shampoo\b", r"\bpet care\b", r"\bperawatan hewan\b")),
    ("Elektronik & Gadget", "Smartphone/tablet", (r"\bsmartphone\b", r"\biphone\b", r"\bhandphone\b", r"\bponsel\b", r"\bipad\b")),
    ("Elektronik & Gadget", "Komputer/laptop", (r"\blaptop\b", r"\bnotebook\b", r"\bmonitor\b", r"\bkeyboard\b", r"\bmouse wireless\b", r"\bssd\b")),
    ("Elektronik & Gadget", "Audio/aksesori elektronik", (r"\bheadphone\b", r"\bearphone\b", r"\bearbuds\b", r"\bcharger\b", r"\bkabel usb\b", r"\bpower ?bank\b", r"\bspeaker bluetooth\b", r"\bflashdisk\b")),
    ("Elektronik & Gadget", "TV/kamera/perangkat lain", (r"\btelevisi\b", r"\btv led\b", r"\bprinter\b", r"\bkamera digital\b", r"\bperangkat elektronik\b")),
    ("Fashion & Aksesori", "Pakaian", (r"\bkaos\b", r"\bkemeja\b", r"\bdress\b", r"\bcelana jeans\b", r"\bjaket\b")),
    ("Fashion & Aksesori", "Sepatu/sandal", (r"\bsepatu\b", r"\bshoes?\b", r"\bsneakers?\b", r"\bsandal\b", r"\bnike\b", r"\badidas\b", r"\bpuma\b", r"\buniqlo\b", r"\bzara\b", r"\bh and m\b")),
    ("Fashion & Aksesori", "Tas/dompet/aksesori", (r"\btas wanita\b", r"\btas ransel\b", r"\bhandbag\b", r"\bdompet\b", r"\bjam tangan\b", r"\bscarf\b")),
    ("Otomotif", "Oli/perawatan kendaraan", (r"\boli mesin\b", r"\boli motor\b", r"\boli mobil\b", r"\bcar care\b", r"\bservis kendaraan\b")),
    ("Otomotif", "Aksesori/suku cadang", (r"\bsuku cadang\b", r"\bspare ?part\b", r"\baksesori mobil\b", r"\baksesori motor\b", r"\bhelm(?:et)?\b", r"\bban motor\b")),
    ("Pendidikan, Buku & Alat Tulis", "Buku", (r"\bnovel\b", r"\bbuku pelajaran\b", r"\bbuku tulis\b", r"\bkamus\b")),
    ("Pendidikan, Buku & Alat Tulis", "Alat tulis/sekolah", (r"\bpena\b", r"\bpulpen\b", r"\bpensil\b", r"\bspidol\b", r"\balat tulis\b", r"\bstationery\b", r"\bback to school\b")),
    ("Perawatan Tubuh & Mandi", "Sabun mandi/body wash", (r"\bsabun mandi\b", r"\bbody wash\b", r"\bshower gel\b", r"\bsabun cair\b", r"\blifebuoy\b", r"\bdetol body\b")),
    ("Perawatan Tubuh & Mandi", "Deodorant", (r"\bdeodorant\b", r"\bdeodoran\b", r"\bantiperspirant\b")),
    ("Perawatan Tubuh & Mandi", "Body lotion/perawatan tubuh", (r"\bbody lotion\b", r"\bbody cologne\b", r"\bbody scrub\b", r"\bhand cream\b", r"\bhand sanitizer\b", r"\bhandwash\b")),
    ("Perawatan Rambut & Kulit", "Sampo", (r"\bsampo\b", r"\bshampoo\b")),
    ("Perawatan Rambut & Kulit", "Kondisioner/perawatan rambut", (r"\bkondisioner\b", r"\bconditioner\b", r"\bhair serum\b", r"\bhair mask\b", r"\btonik rambut\b")),
    ("Perawatan Rambut & Kulit", "Pembersih wajah/skincare", (r"\bfacial wash\b", r"\bface wash\b", r"\bfacial cleanser\b", r"\bcleanser wajah\b", r"\bskincare\b", r"\bskin care\b", r"\bmoisturizer\b", r"\bpelembap wajah\b", r"\btoner wajah\b", r"\bserum wajah\b", r"\bretinol\b", r"\bsunscreen\b", r"\bsunblock\b")),
    ("Kecantikan & Kosmetik", "Makeup wajah", (r"\bfoundation\b", r"\bbedak\b", r"\bblush ?on\b")),
    ("Kecantikan & Kosmetik", "Makeup mata", (r"\beyeliner\b", r"\bmascara\b")),
    ("Kecantikan & Kosmetik", "Produk bibir", (r"\blipstick\b", r"\blip tint\b", r"\blip cream\b")),
    ("Kecantikan & Kosmetik", "Parfum/fragrance", (r"\bparfum\b", r"\beau de parfum\b", r"\bfragrance\b", r"\bbody mist\b")),
    ("Makanan & Bahan Pokok", "Beras", (r"\bberas\b", r"\brace\b")),
    ("Makanan & Bahan Pokok", "Minyak goreng", (r"\bminyak goreng\b", r"\bcooking oil\b")),
    ("Makanan & Bahan Pokok", "Gula/garam/bumbu", (r"\bgula\b", r"\bgaram\b", r"\bbumbu\b", r"\bkecap\b", r"\bsaus\b", r"\bkaldu\b", r"\bsantan\b")),
    ("Makanan & Bahan Pokok", "Tepung/margarin/telur", (r"\btepung\b", r"\bmargarin\b", r"\bmentega\b", r"\btelur\b")),
    ("Minuman & Produk Susu", "Susu/produk susu", (r"\bsusu\b", r"\byogh?urt\b", r"\bkeju\b", r"\bkrimer\b", r"\buht\b", r"\bfull cream\b")),
    ("Minuman & Produk Susu", "Kopi", (r"\bkopi\b", r"\bcoffee\b", r"\bcappuccino\b", r"\blatte\b")),
    ("Minuman & Produk Susu", "Teh", (r"\bteh\b", r"\btea\b")),
    ("Minuman & Produk Susu", "Air mineral/minuman ringan", (r"\bair mineral\b", r"\bair putih\b", r"\bjus\b", r"\bjuice\b", r"\bsoda\b", r"\bminuman\b", r"\bsirup\b", r"\bisotonik\b", r"\bsoft drink\b", r"\bcola\b", r"\bminuman serbuk\b")),
    ("Restoran & Makanan Siap Saji", "Menu/paket restoran", (r"\bmenu\b", r"\bpaket makanan\b", r"\bpaket nasi\b", r"\bpaket hemat\b", r"\bset menu\b", r"\bcombo meal\b", r"\bbento\b", r"\bburger\b", r"\bsandwich\b", r"\bsushi\b", r"\bramen\b", r"\budon\b", r"\bpizza\b", r"\bpasta\b", r"\bayam goreng\b", r"\bfried chicken\b", r"\brice bowl\b", r"\bayam crispy\b", r"\bkentang goreng\b", r"\bhotdog\b", r"\bsteak\b", r"\bdimsum\b", r"\bdim sum\b", r"\bbakmi\b", r"\bmie ayam\b", r"\bbakso\b", r"\bsiomay\b", r"\bsoto\b", r"\bcroissant\b", r"\bdonat\b", r"\bdonut\b", r"\broti isi\b", r"\bmochi\b", r"\bparis baguette\b", r"\brotio\b", r"\broti'o\b", r"\bkopi kenangan\b", r"\bstarbucks\b", r"\bhokben\b", r"\bkfc\b", r"\bmcdonald\b", r"\bmcd\b", r"\bsubway\b", r"\bwingstop\b", r"\bbakmi gm\b", r"\bj\.co\b", r"\bgokana\b", r"\bmarugame\b", r"\byoshinoya\b", r"\bpizza hut\b", r"\bphd\b", r"\bfore coffee\b", r"\bjanji jiwa\b", r"\britchee?se\b", r"\bgenki sushi\b", r"\bchatime\b", r"\bmixue\b", r"\bburger bangor\b", r"\bpoint coffee\b", r"\bsolaria\b", r"\bes teler 77\b", r"\ba w\b")),
    ("Produk Segar & Beku", "Buah", (r"\bbuah\b", r"\balpukat\b", r"\bapel\b", r"\bapples?\b", r"\bpisang\b", r"\banggur\b", r"\bsemangka\b", r"\bpepaya\b", r"\bmelon\b")),
    ("Produk Segar & Beku", "Sayur", (r"\bsayur(?:an)?\b", r"\bwortel\b", r"\bbayam\b", r"\bkangkung\b", r"\bbawang\b", r"\bcabai\b", r"\bkentang segar\b")),
    ("Produk Segar & Beku", "Daging/ikan/seafood", (r"\bdaging\b", r"\bikan segar\b", r"\bseafood\b", r"\budang\b", r"\bsalmon\b", r"\bayam mentah\b")),
    ("Produk Segar & Beku", "Makanan beku", (r"\bfrozen food\b", r"\bmakanan beku\b", r"\bnugget\b", r"\bsosis beku\b", r"\bes krim\b", r"\bice cream\b")),
    ("Snack & Makanan Kemasan", "Mi instan", (r"\bmi instan\b", r"\bmie instan\b", r"\bindomie\b", r"\bmie sedaap\b", r"\bpop mie\b")),
    ("Snack & Makanan Kemasan", "Snack/keripik", (r"\bkeripik\b", r"\bchips\b", r"\bsnack\b", r"\bpopcorn\b", r"\bkacang\b")),
    ("Snack & Makanan Kemasan", "Biskuit/wafer/roti kemasan", (r"\bbiskuit\b", r"\bbiscuit\b", r"\bcookies\b", r"\bwafer\b", r"\broti tawar\b", r"\broti kemasan\b", r"\bmalkist\b")),
    ("Snack & Makanan Kemasan", "Cokelat/permen", (r"\bcokelat\b", r"\bchocolate\b", r"\bpermen\b", r"\bcandy\b")),
    ("Snack & Makanan Kemasan", "Makanan kemasan lainnya", (r"\bmakanan kaleng\b", r"\babon\b", r"\bgranola bar\b", r"\bcereal bar\b")),
    ("Pembayaran, Cashback & Loyalitas", "Cashback", (r"\bcashback\b", r"\bcash back\b")),
    ("Pembayaran, Cashback & Loyalitas", "Pembayaran/bank/QRIS", (r"\bqris\b", r"\bkartu kredit\b", r"\bkartu debit\b", r"\bpromo bank\b", r"\bpaylater\b", r"\bcicilan\b", r"\bmybca\b")),
    ("Pembayaran, Cashback & Loyalitas", "Poin/reward/loyalitas", (r"\bpoin reward\b", r"\breward points?\b", r"\bloyalty points?\b", r"\bmember day\b", r"\brewards?\b")),
    ("Belanja Online & Marketplace", "Marketplace/ongkir", (r"\bgratis ongkir\b", r"\bvoucher ongkir\b", r"\bongkos kirim\b")),
    ("Voucher & Gift Card", "Voucher/kupon/gift card", (r"\bgift card\b", r"\bgiftcard\b", r"\bvoucher\b", r"\bkupon\b")),
    ("Belanja Online & Marketplace", "Marketplace/ongkir", (r"\bgratis ongkir\b", r"\bvoucher ongkir\b", r"\bmarketplace\b", r"\bshopee\b", r"\btokopedia\b", r"\bblibli\b", r"\blazada\b", r"\btiktok shop\b", r"\bzalora\b", r"\bflash sale online\b", r"\bcheckout murah\b")),
    ("Perjalanan & Transportasi", "Tiket/hotel/transportasi", (r"\btiket pesawat\b", r"\btiket kereta\b", r"\bhotel\b", r"\btraveloka\b", r"\btiket\.com\b", r"\bbooking\.com\b", r"\bairlines?\b", r"\btransportasi daring\b", r"\bgojek\b", r"\bgrabcar\b", r"\bgrab bike\b", r"\bsewa mobil\b", r"\brental mobil\b")),
    ("Hiburan & Rekreasi", "Tiket hiburan/rekreasi", (r"\bbioskop\b", r"\bcinema\b", r"\bxxi\b", r"\bcgv\b", r"\btaman rekreasi\b", r"\btheme park\b", r"\bwaterpark\b", r"\bkonser\b", r"\btiket acara\b", r"\bplayground\b", r"\btimezone\b", r"\bkaraoke\b")),
    ("Telekomunikasi & Digital", "Pulsa/paket data/internet", (r"\bpulsa\b", r"\bpaket data\b", r"\bkuota internet\b", r"\bpaket internet\b", r"\bprovider internet\b", r"\bindihome\b", r"\bindosat\b", r"\btelkomsel\b", r"\bxl axiata\b", r"\bby u\b", r"\bsmartfren\b", r"\btri indonesia\b")),
    ("Telekomunikasi & Digital", "Langganan digital", (r"\bnetflix\b", r"\bspotify\b", r"\bsubscription digital\b", r"\blangganan streaming\b", r"\bcloud storage\b")),
    ("Layanan & Jasa", "Servis/perawatan/jasa", (r"\bjasa\b", r"\bservice center\b", r"\bservis kendaraan\b", r"\bcuci sepatu\b", r"\blaundry kiloan\b", r"\bbersih rumah\b", r"\bperawatan rumah\b", r"\basuransi\b", r"\bsalon\b", r"\bbarbershop\b", r"\bmembership\b")),
]

def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.casefold().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%+.'-]+", " ", text)).strip()


# Python's built-in re cache is intentionally small (512 patterns). This
# classifier uses well over that number of patterns, so repeatedly calling
# re.search(pattern, ...) churns the cache during workbook export. Keep our
# own bounded-by-taxonomy cache of compiled expressions instead.
_COMPILED_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    if not text or not patterns:
        return False

    for pattern in patterns:
        compiled = _COMPILED_PATTERN_CACHE.get(pattern)
        if compiled is None:
            compiled = re.compile(pattern, flags=re.IGNORECASE)
            _COMPILED_PATTERN_CACHE[pattern] = compiled
        if compiled.search(text):
            return True

    return False


def _match_rules(text: str, rules: list[tuple[str, str, tuple[str, ...]]]):
    for category, subcategory, patterns in rules:
        if _matches(text, patterns):
            return category, subcategory
    return None


def _generic_campaign_subcategory(text: str) -> str:
    if _matches(text, (r"\b(?:multi[- ]merchant|lintas merchant|banyak merchant|multi[- ]kategori)\b",)):
        return "Promo Multi-Merchant"
    if _matches(text, (r"\b(?:beberapa|berbagai|rangkuman|kumpulan|\d+\s+promo)\b", r"\bpromo\s+(?:dari|di)\s+(?:berbagai|sejumlah)\s+merchant\b")):
        return "Rangkuman Beberapa Promo"
    return "Kampanye Umum"



def _is_non_product_phrase(product: str) -> bool:
    """Reject FAQ or explanatory prose accidentally parsed as a SKU."""
    text = _norm(product)

    if not text:
        return False

    if _matches(text, (
        r"^q\s+apakah ada promo\b",
        r"^apakah ada promo\b",
        r"^a\b.*\b(?:ditawarkan seharga|dijual seharga|harga)\b",
        r"\bseluruh jenis pembayaran dapat digunakan\b",
        r"\bpelanggan tidak dibatasi\b",
        r"\bmetode pembayaran tertentu\b",
        r"\buntuk promo ini\b",
        r"\bkhusus .* harga\b",
        r"\bsyarat dan ketentuan berlaku\b",
        r"\binformasi lebih lanjut\b",
        r"\bcek syarat dan ketentuan\b",
    )):
        return True

    # Text much longer than a product title, with sentence-like wording,
    # is usually a parsed paragraph rather than a merchandise item.
    if len(text) > 130 and _matches(text, (
        r"\bsehingga\b",
        r"\bpengguna dapat\b",
        r"\bpembayaran dapat digunakan\b",
        r"\bpelanggan\b",
        r"\btertentu\b",
        r"\bdi mana\b",
    )):
        return True

    return False



def _provided_name_override(p: str):
    """High-confidence rules for recurring products in the review queue.

    These patterns are intentionally based on product type or a strong
    brand/category combination. Flavor words such as jeruk/lemon, marketing
    terms such as hijab/parfum, and package words must not decide a category
    on their own.
    """
    if not p:
        return None

    # These are purchase conditions, not sellable product SKUs.
    if _matches(p, (
        r"^(?:minimum pembelanjaan|minimum belanja|minimum transaksi|"
        r"min\.?\s*belanja|min\.?\s*pembelanjaan)$",
    )):
        return (GENERAL, "Syarat minimum belanja")

    # Explicit dishwashing product wins over incidental citrus words.
    if _matches(p, (
        r"\bsparkle\b.*\bdish\s*wash(?:ing)?\b",
        r"\bdish\s*wash(?:ing)?\b",
        r"\bdishwash(?:ing)?\b",
        r"\bsabun cuci piring\b",
        r"\bpencuci piring\b",
        r"\bekonomi\b.*\b(liquid|refill|jeruk nipis)\b",
    )):
        return ("Kebersihan Rumah Tangga", "Sabun cuci piring")

    # Laundry and stain-removal product identity wins over scent/perfume words.
    if _matches(p, (
        r"\boxyklin\b",
        r"\bbukrim\b.*\boxyklin\b",
        r"\bsayang\b.*\bliquid\b.*\b2\s*in\s*1\b",
        r"\bsayang liquid\b",
        r"\b(doube|double)\b.*\b(detergent|deterjen|detergen|stain|noda)\b",
        r"\bfleckz\b.*\bsabun krim\b",
        r"\bsabun krim\b.*\b(laundry|cucian|pakaian)\b",
    )):
        if _matches(p, (r"\b(oxyclin|oxyklin|stain|noda|vanish)\b",)):
            return ("Cuci Pakaian", "Penghilang noda pakaian")
        if _matches(p, (r"\bsabun krim\b",)):
            return ("Cuci Pakaian", "Sabun krim cucian")
        return ("Cuci Pakaian", "Deterjen")

    # Toilet cleaners and insect-control products remain distinct subcategories.
    if _matches(p, (
        r"\b(harpic|vixal)\b",
        r"\bpembersih toilet\b",
        r"\bpembersih kloset\b",
        r"\btoilet cleaner\b",
        r"\btoilet bowl\b",
        r"\bpembersih kerak toilet\b",
    )):
        return ("Kebersihan Rumah Tangga", "Pembersih toilet/kloset")

    if _matches(p, (
        r"\bkapur ajaib\b",
        r"\banti nyamuk\b",
        r"\bobat nyamuk\b",
        r"\banti serangga\b",
        r"\bpembasmi serangga\b",
        r"\binsektisida\b",
        r"\bvape\b.*\b(aeo|aero|aerosol|orange breeze)\b",
    )):
        return ("Kebersihan Rumah Tangga", "Anti nyamuk/serangga")

    # Adult incontinence products are not infant diapers.
    if _matches(p, (
        r"\blifree\b.*\b(pants|popok|ekstra serap|tipis|nyaman)\b",
        r"\bpopok dewasa\b",
        r"\badult diaper\b",
        r"\binkontinensia\b",
    )):
        return ("Kesehatan & Farmasi", "Popok dewasa/inkontinensia")

    # Infant-branded cologne/body care must be assigned before general body-care cues.
    if _matches(p, (
        r"\bmy baby\b",
        r"\bcussons baby\b",
        r"\bbaby cologne\b",
        r"\bbaby body cologne\b",
        r"\bkolonye bayi\b",
        r"\bjohnson.?s\b.*\b(kolonye bayi|baby cologne|swing h berries|happy berries)\b",
        r"\bbebio\b.*\bminyak telon\b",
        r"\bminyak telon\b",
    )):
        return ("Bayi & Anak", "Perawatan kulit/tubuh bayi")

    # Baby/child products: separate diapers, formula/growth milk, MPASI and care.
    if _matches(p, (
        r"\bsweety\b.*\b(pants|tape|silver|bronze|skin premium|max pro)\b",
        r"\bmerries\b.*\b(pants|good skin|popok)\b",
        r"\bmakuku\b.*\b(pants|comfort fit|dry care)\b",
        r"\bpopok bayi\b",
        r"\bpampers\b.*\b(pants|diapers?)\b",
        r"\bdiapers? bayi\b",
    )):
        return ("Bayi & Anak", "Popok")

    if _matches(p, (r"\bpromina\b", r"\bmpasi\b", r"\bsnack bayi\b")):
        return ("Bayi & Anak", "MPASI/snack bayi")

    if _matches(p, (
        r"\bvidoran\b.*\b(xmart|1\+|3\+|madu|vanilla)\b",
        r"\bchil\s*[- ]?kid\b",
        r"\bmorigro\b.*\b(madu|vanilla|800\s*g|1\+|3\+)\b",
        r"\bsusu formula\b",
        r"\bsusu pertumbuhan\b",
    )):
        return ("Bayi & Anak", "Susu pertumbuhan anak")

    if _matches(p, (
        r"\bbebio\b.*\bminyak telon\b",
        r"\bkolonye bayi\b",
        r"\bjohnson.?s\b.*\b(kolonye bayi|baby cologne|swing h berries|happy berries)\b",
        r"\bbaby cologne\b",
        r"\bminyak telon\b",
        r"\bperawatan bayi\b",
    )):
        return ("Bayi & Anak", "Perawatan kulit/tubuh bayi")

    # Feminine hygiene is placed with personal hygiene, not fashion or baby care.
    if _matches(p, (
        r"\bpokana\b.*\b(wings|day|night|flou)\b",
        r"\bflou\b.*\b(wings|day|night)\b",
        r"\bpembalut wanita\b",
        r"\bsanitary pad\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Pembalut/feminine hygiene")

    # Personal-care product identity beats unrelated brand variants.
    # Keep the subcategory precise: deodorant, soap/body wash and bath
    # accessories are different types of goods.
    if _matches(p, (
        r"\b(nivea|posh)\b.*\b(roll\s*on|deo|deodorant|deodoran|antiperspirant|deo lotion)\b",
        r"\bdeo\s*lotion\b",
        r"\bdeodorant\b",
        r"\bdeodoran\b",
        r"\broll\s*on\b",
        r"\bbody cologne\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Deodorant/kebersihan tubuh")

    if _matches(p, (
        r"\bsabun mawar\b",
        r"\bsabun mandi\b",
        r"\bbody wash\b",
        r"\bshower gel\b",
        r"\bbath gel\b",
        r"\bjohnson.?s\b.*\bh\s*(?:and|&)\s*b\b.*\b(aloe|refill|lotion)\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Sabun mandi/body wash")

    if _matches(p, (
        r"\bkorek kuping\b",
        r"\bcotton buds?\b",
        r"\bear buds?\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Cotton buds/kebersihan telinga")

    if _matches(p, (
        r"\bshower puff\b",
        r"\bsabut mandi\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Aksesori mandi")

    # Face care and sunscreen must not be sent to generic review/cosmetics.
    if _matches(p, (
        r"\bviva\b.*\b(face tonic|tonic|toner|cleanser|bengkoang|bengkuang)\b",
        r"\bface tonic\b",
        r"\bfacial tonic\b",
        r"\bfacial cleanser\b",
        r"\bface cleanser\b",
        r"\bfacial foam\b",
        r"\b(nivea|sun protect|sun oil)\b.*\b(spf\s*50|spf50|sun protect|sun oil|facial foam)\b",
        r"\bsunscreen\b",
        r"\bsunblock\b",
    )):
        return ("Perawatan Rambut & Kulit", "Perawatan wajah/sunscreen")

    # Therapeutic patches and herbal medicinal products.
    if _matches(p, (
        r"\bsalonpas\b.*\bkoyo\b",
        r"\bkoyo\b.*\b(extra hot|salonpas|pereda nyeri)\b",
        r"\bbejo\b.*\b(jahe merah|herbal)\b",
    )):
        return ("Kesehatan & Farmasi", "Koyo/obat herbal")

    # Adult vitamin C beverage, separate from tablet/supplement forms.
    if _matches(p, (
        r"\bhemaviton\b.*\bc1000\b.*\b(330\s*ml|lemon|health drink)\b",
        r"\bhemaviton c1000\b",
        r"\bc1000 health drink\b",
    )):
        return ("Minuman & Produk Susu", "Minuman vitamin")

    # Baby growth milk vs adult nutrition milk.
    if _matches(p, (
        r"\b(vidoran|chil\s*[- ]?kid|morigro)\b",
    )):
        return ("Bayi & Anak", "Susu pertumbuhan anak")
    if _matches(p, (
        r"\bentrasol\b",
        r"\btiga sapi\b.*\b(kkm|susu kental manis)\b",
        r"\bsusu kental manis\b",
        r"\bcimory\b.*\b(eat milk|milk)\b",
    )):
        return ("Minuman & Produk Susu", "Susu/produk susu")

    # Drinks and powdered beverage products are identified before generic snack cues.
    if _matches(p, (
        r"\bgery chocolatos gold\b.*\b350\s*g\b",
        r"\bchocolatos gold original\b",
        r"\bminuman cokelat bubuk\b",
        r"\bhemaviton c1000\b",
    )):
        return ("Minuman & Produk Susu", "Minuman cokelat/vitamin")

    # Fresh fruit/vegetables and meat rely on explicit item-type words, not flavour.
    if _matches(p, (
        r"\btomat cherry\b",
        r"\bpear xiang\b",
        r"\bjeruk lemon\b",
        r"\blemon impor\b",
        r"\bkiwi\b",
        r"\bmangga\b",
        r"\bjamur enoki\b",
        r"\bkembang kol\b",
        r"\bbeef saikoro\b",
        r"\bbeef rib\b",
        r"\bayam broiler\b",
        r"\bayam broiler utuh\b",
        r"\bpotato nuggets?\b",
        r"\bfrench fries\b",
        r"\bgolden farm\b.*\b(potato|fries|nugget|french fries)\b",
        r"\bseafoodking\b.*\b(chikuwa|seafood)\b",
        r"\bchikuwa\b",
        r"\b(hypermart|lotte mart|generic)\b.*\b(ayam broiler|beef rib|beef saikoro)\b",
        r"\b(fiest[a]?|belfoods|hemato gold|yummy choice)\b.*\b(naget|nugget|karaage|spicy wing|chicken tender|cheesy bombs|tori ball|rice bites|egg roll)\b",
        r"\b(naget ayam|nugget ayam|karaage|chicken tender|spicy wing|cheesy bombs)\b",
        r"\b(ayam broiler|daging sapi|beef rib|beef saikoro)\b",
        r"\b(lotte mart|hypermart)\b.*\bayam\b.*\bfrozen\b",
    )):
        return ("Produk Segar & Beku", "Buah/sayur/daging/produk beku")

    # Adult and toddler nutritional products above are handled separately.
    # Staples and cooking ingredients stay distinct from snacks and ready-to-eat food.
    if _matches(p, (
        r"\btropicana slim\b.*\b(sweetener|sweetener no calori|no calori|diabetics)\b",
        r"\b(sweetener|pemanis rendah kalori|pemanis tanpa kalori)\b",
    )):
        return ("Makanan & Bahan Pokok", "Pemanis rendah kalori")

    if _matches(p, (
        r"\b(borges|tropicana slim)\b.*\bolive oil\b",
        r"\bolive oil\b",
        r"\bminyak zaitun\b",
    )):
        return ("Makanan & Bahan Pokok", "Minyak zaitun/minyak masak")

    if _matches(p, (
        r"\bdel monte\b.*\bketchup\b",
        r"\bketchup\b",
        r"\bsaus\b|\bsambal\b|\bkecap\b|\bmayones\b|\bsaus tiram\b",
    )):
        return ("Makanan & Bahan Pokok", "Saus/sambal/bumbu")

    if _matches(p, (r"\bmaestro\b.*\bwijen sangrai\b", r"\bwijen sangrai\b")):
        return ("Makanan & Bahan Pokok", "Bahan masak/topping")

    if _matches(p, (r"\bquaker\b.*\b(oats?|oatmeal)\b", r"\boatmeal\b")):
        return ("Makanan & Bahan Pokok", "Oatmeal/sereal sarapan")

    if _matches(p, (r"\bmy vla\b.*\bvla instan\b", r"\bvla instan\b")):
        return ("Makanan & Bahan Pokok", "Bahan dessert/vla instan")

    if _matches(p, (r"\bovomaltine\b.*\bcrunchy cream\b", r"\bselai\b.*\b(cokelat|chocolate|kacang)\b")):
        return ("Makanan & Bahan Pokok", "Selai/olesan")

    # Packaged snacks; the explicitly identified powdered Chocolatos product above
    # remains a beverage while Chocolatos wafers/snacks remain food products.
    if _matches(p, (
        r"\bnutella\b.*\bb-ready\b",
        r"\bportos\b.*\bsingle bite\b",
        r"\bdelfi\b.*\btake it share\b",
        r"\bmilkita\b.*\bmilky bar\b",
        r"\bmonde\b.*\begg roll\b",
        r"\bchocolatos\b.*\b(wafer|snack|stick|coated wafer)\b",
        r"\btao kae noi\b",
        r"\blemonilo\b.*\b(bronis|brownies|krispi|crunchy)\b",
        r"\btaiko\b.*\bkrezz\b",
        r"\bdelixios\b.*\broti sobek\b",
        r"\b(gery|portos|delfi|milkita|monde|nutella|tao kae noi|lemonilo|taiko)\b.*\b(wafer|b-ready|single bite|milky bar|egg roll|snack|camilan|brownies|bronis)\b",
        r"\byummy choice\b.*\b(okane|egg roll|tori ball|spicy chicken tender)\b",
        r"\balfamidi\b.*\bsamosa\b",
        r"\bdeltafoods\b.*\btongkol\b",
    )):
        return ("Snack & Makanan Kemasan", "Snack/makanan kemasan/siap saji")

    # Named pet-food product.
    if _matches(p, (
        r"\bfursure\b.*\bpakan kucing\b",
        r"\bpakan kucing\b",
        r"\bcat food\b",
        r"\bpet food\b",
    )):
        return ("Hewan Peliharaan", "Makanan hewan")

    # Batteries and household lighting are electrical products, but retain
    # distinct subcategories to avoid mixing AA/AAA cells with LED bulbs.
    if _matches(p, (
        r"\babc alkaline\b.*\blr-0[36]\b",
        r"\blr-0[36]\b.*\b(aa|aaa)\b",
        r"\balkaline\b.*\b(aa|aaa)\b",
    )):
        return ("Elektronik & Gadget", "Baterai/perangkat listrik")

    if _matches(p, (
        r"\bphilips\b.*\bled\b.*\b(11w|watt)\b",
        r"\bled\b.*\b(11w|watt)\b",
    )):
        return ("Elektronik & Gadget", "Lampu LED")

    # General body-care brand/product type catches explicit names from the list.
    if _matches(p, (
        r"\bnivea\b.*\b(roll on|pearl|derma control|deo)\b",
        r"\bposh deo\b",
        r"\bdeo roll on\b",
        r"\bdeo lotion\b",
        r"\bminyak telon\b",
        r"\bsabun mawar\b",
        r"\bkorek kuping\b",
        r"\bshower puff\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Kebersihan tubuh/deodorant")

    return None


def _strict_product_override(product: str):
    """High-confidence product rules; specific item type outranks flavour/marketing words."""
    p = _norm(product)

    if not p:
        return None

    # Review-queue overrides: high-confidence item type/brand rules take priority.
    provided_override = _provided_name_override(p)
    if provided_override:
        return provided_override

    # Explicit baby-care brands/forms belong to Bayi & Anak rather than
    # the general body-care category. Avoid matching flavour names like "Baby Bears".
    if _matches(p, (
        r"^my baby\b",
        r"^cussons baby\b",
        r"\bbaby hair and body wash\b",
        r"\bbaby hair and body\b",
        r"\bbaby shampoo\b",
        r"\bbaby bath\b",
        r"\bbaby lotion\b",
        r"\bbaby cologne\b",
        r"\bbaby powder\b",
        r"\bbaby soap\b",
        r"\bbaby oil\b",
        r"\btisu bayi\b",
        r"\bbaby wipes?\b",
    )):
        return ("Bayi & Anak", "Perawatan kulit/tubuh bayi")

    # Toilet and bathroom cleaners must not share a subcategory with insecticides.
    if _matches(p, (
        r"\bharpic\b",
        r"\bvixal\b",
        r"\bpembersih toilet\b",
        r"\btoilet cleaner\b",
        r"\bpembersih kloset\b",
        r"\btoilet bowl\b",
        r"\btoilet colour ball\b",
        r"\bpembersih porselen\b",
        r"\bporcelain cleaner\b",
        r"\bpembersih kamar mandi\b",
        r"\bbathroom cleaner\b",
        r"\bpembersih kerak\b",
    )):
        return ("Kebersihan Rumah Tangga", "Pembersih toilet/kloset")

    # Pest control is a separate household category.
    if _matches(p, (
        r"\banti nyamuk\b",
        r"\bobat nyamuk\b",
        r"\banti serangga\b",
        r"\bpembasmi serangga\b",
        r"\binsektisida\b",
        r"\binsecticide\b",
        r"\bmosquito repellent\b",
        r"\bbaygon\b",
        r"\bhit anti nyamuk\b",
        r"\bvape elect\b",
        r"\bvape aerosol\b",
        r"\bvape liquid anti nyamuk\b",
        r"\bpembunuh serangga\b",
    )):
        return ("Kebersihan Rumah Tangga", "Anti nyamuk/serangga")

    # Automotive scent products are not cosmetic perfumes.
    if _matches(p, (
        r"\bairpro\b",
        r"\bcar parfume?\b",
        r"\bcar perfume\b",
        r"\bcar fragrance\b",
        r"\bpengharum mobil\b",
        r"\bpewangi mobil\b",
    )):
        return ("Otomotif", "Pengharum/perawatan mobil")

    # Fabric softeners / clothes perfumes take precedence over detergent
    # brand names such as So Klin; explicit detergent SKUs still match below.
    if _matches(p, (
        r"\bpewangi pakaian\b",
        r"\bpelembut pakaian\b",
        r"\bfabric softener\b",
        r"\bsoftener pakaian\b",
        r"\bmolto\b",
        r"\bdowny\b",
    )):
        return ("Cuci Pakaian", "Pelembut/pewangi pakaian")

    # Laundry type is stronger than fragrance words such as "Eau de Parfum".
    if _matches(p, (
        r"\bdetergen\b",
        r"\bdeterjen\b",
        r"\bdetergent\b",
        r"\bsoftergent\b",
        r"\bliq det\b",
        r"\bliquid det\b",
        r"\bgentle gen liq\b",
        r"\bgentle gen liquid det\b",
        r"\bdet all var\b",
        r"\bdet powder\b",
        r"\bjaz1 det\b",
        r"\bdet pesona\b",
        r"\brinso\b",
        r"\bso klin\b",
        r"\bso soft detergent\b",
    )):
        return ("Cuci Pakaian", "Deterjen")

    if _matches(p, (
        r"\bpenghilang noda\b",
        r"\bstain remover\b",
        r"\bvanish\b",
    )):
        return ("Cuci Pakaian", "Penghilang noda pakaian")

    if _matches(p, (
        r"\brapika\b",
        r"\bpelicin pakaian\b",
        r"\bstarch spray\b",
        r"\bkanji pakaian\b",
        r"\biron spray\b",
    )):
        return ("Cuci Pakaian", "Pelicin pakaian")

    # Other household cleaning items must beat scent/flavour tokens.
    if _matches(p, (
        r"\bsabun cuci piring\b",
        r"\bpencuci piring\b",
        r"\bdishwashing\b",
        r"\bdishwash\b",
        r"\bsunlight\b",
        r"\bmama lemon\b",
        r"\bmama lime\b",
        r"\blarisst pencuci piring\b",
    )):
        return ("Kebersihan Rumah Tangga", "Sabun cuci piring")

    if _matches(p, (
        r"\bkamper\b",
        r"\bdeodorizer\b",
        r"\bpengharum ruangan\b",
        r"\bair freshener\b",
        r"\broom fragrance\b",
        r"\btoilet deodorizer\b",
        r"\bglade\b",
        r"\bbathroom .*mist\b",
        r"\broom spray\b",
    )):
        return ("Kebersihan Rumah Tangga", "Pengharum/deodorizer ruangan")

    if _matches(p, (
        r"\bpembersih lantai\b",
        r"\bpembersih kaca\b",
        r"\bpembersih rumah\b",
        r"\bdisinfektan\b",
        r"\bdisinfectant\b",
        r"\bkarbol\b",
        r"\bwipol\b",
        r"\bporstex\b",
    )):
        return ("Kebersihan Rumah Tangga", "Pembersih rumah")

    # Fragrance/product-format cues outrank flavour words such as Tea or
    # Cool Water. Laundry-specific products already matched above.
    if _matches(p, (
        r"\beau de parfum\b",
        r"\beau de toilette\b",
        r"\bedp\b",
        r"\bedt\b",
        r"\bextrait\b",
        r"\bparfum\b",
        r"\bperfume\b",
        r"\bfragrance mist\b",
        r"\bbody mist\b",
        r"\bfragrance\b",
    )):
        return ("Kecantikan & Kosmetik", "Parfum/fragrance")

    # Micellar/cleansing water and sunscreen are skincare, not beverages.
    if _matches(p, (
        r"\bmicellar water\b",
        r"\bmicellar cleansing water\b",
        r"\bcleansing water\b",
        r"\bwater sunscreen\b",
        r"\bsunscreen\b",
        r"\bsunblock\b",
        r"\bfacial cleanser\b",
        r"\bface cleanser\b",
        r"\bmicellar cleanser\b",
        r"\bcleansing oil\b",
        r"\bmakeup remover\b",
        r"\bmake up remover\b",
        r"\bmakeup remover water\b",
        r"\bskincare\b",
        r"\bskin care\b",
    )):
        return ("Perawatan Rambut & Kulit", "Pembersih wajah/skincare")

    # Ice cream remains frozen dessert even when the title mentions
    # Cookies & Cream, snack, chocolate or other food flavours.
    if _matches(p, (
        r"\bes krim\b",
        r"\bice cream\b",
        r"\bfrozen dessert\b",
        r"\bgelato\b",
        r"\bsorbet\b",
        r"\baice\b.*\bice cream\b",
        r"\bwall.?s\b.*\bice cream\b",
        r"\bmagnum\b.*\bice cream\b",
        r"\bfrost bite\b",
        r"\bjoyday\b.*\bice cream\b",
        r"\bglico wings\b.*\bice cream\b",
    )):
        return ("Produk Segar & Beku", "Es krim/dessert beku")

    # Staple baking ingredients outrank snack/flavour words.
    if _matches(p, (
        r"^blue band\b",
        r"\bmargarin\b",
        r"\bmargarine\b",
        r"^mentega\b",
        r"^butter\b",
    )):
        return ("Makanan & Bahan Pokok", "Margarin/mentega/bahan baking")

    # Hair care outranks "hijab" when hijab is just the shampoo variant.
    if _matches(p, (
        r"\bshampoo\b",
        r"\bsampo\b",
        r"\bshp\b",
        r"\banti dandruff\b",
        r"\banti ketombe\b",
        r"\bhair tonic\b",
        r"\bhair serum\b",
        r"\bhair mask\b",
        r"\bhair treatment\b",
    )):
        return ("Perawatan Rambut & Kulit", "Sampo/perawatan rambut")

    if _matches(p, (
        r"\bkondisioner\b",
        r"\bconditioner\b",
        r"\bhair serum\b",
        r"\bhair mask\b",
        r"\btonik rambut\b",
    )):
        return ("Perawatan Rambut & Kulit", "Perawatan rambut")

    # Explicit baby/growth formula belongs to Bayi & Anak, even when
    # words like susu/milk would match the general beverage category.
    if _matches(p, (
        r"\bsusu formula\b",
        r"\bsusu pertumbuhan\b",
        r"\bbebelac\b",
        r"\blactogrow\b",
        r"\blactogen\b",
        r"\bnutrilon\b",
        r"\benfagrow\b",
        r"\benfamil\b",
        r"\bsgm ananda\b",
        r"\bsgm eksplor\b",
        r"\bmorinaga chil\b",
        r"\bfrisian flag primagro\b",
        r"\bdancow fortigro\b",
    )):
        return ("Bayi & Anak", "Susu formula/pertumbuhan")

    # Explicit body-care SKU terms outrank incidental "vitamin" or
    # ingredient/variant names in the product description.
    if _matches(p, (
        r"\bdeodorant\b",
        r"\bdeodoran\b",
        r"\bantiperspirant\b",
        r"\bbody lotion\b",
        r"\bbody wash\b",
        r"\bshower gel\b",
        r"\bsabun mandi\b",
        r"\bbody scrub\b",
        r"\bbody serum\b",
        r"\bhand cream\b",
        r"\broll on deodorant\b",
    )):
        return ("Perawatan Tubuh & Mandi", "Sabun mandi/deodorant/body lotion")

    # Facial wash / face wash are skincare; "Vitamin C" can be a variant.
    if _matches(p, (
        r"\bfacial wash\b",
        r"\bface wash\b",
        r"\bclean & clear\b",
        r"\bpembersih wajah\b",
        r"\bcleanser wajah\b",
    )):
        return ("Perawatan Rambut & Kulit", "Pembersih wajah/skincare")

    # Health/medicine cues beat dosage form, flavor, or words such as "jeruk".
    if _matches(p, (
        r"\bvitamin\b",
        r"\bsuplemen\b",
        r"\bsupplement\b",
        r"\benervon\b",
        r"\bredoxon\b",
        r"\bcdr\b",
        r"\bparacetamol\b",
        r"\bparasetamol\b",
        r"\bobat\b",
        r"\bmedicine\b",
        r"\bobat batuk\b",
        r"\bobat mata\b",
        r"\bkoolfever\b",
        r"\bcounterpain\b",
        r"\bantangin\b",
        r"\btolak angin\b",
        r"\bob herbal\b",
        r"\bimboost\b",
        r"\bemulsion\b",
        r"\bjoybee grow\b",
    )):
        return ("Kesehatan & Farmasi", "Vitamin/suplemen/obat")

    # Baby food/snacks that mention yoghurt/buttermilk as a flavour.
    if _matches(p, (
        r"\bpromina softcorn\b",
        r"\bpromina\b.*\bsoftcorn\b",
        r"\bsnack bayi\b",
        r"\bmakanan ringan bayi\b",
    )):
        return ("Bayi & Anak", "Makanan ringan bayi/anak")

    # This SKU name does not reliably reveal whether the variant is a
    # drink or a spoonable dairy dessert; require review instead of guessing.
    if _matches(p, (r"\bcimory eat milk\b",)):
        return (REVIEW, "Bentuk produk perlu verifikasi")

    # Clear powdered/drink products should not be classified as snacks
    # just because their names mention chocolate, mocha, or fruit flavours.
    if _matches(p, (
        r"\bbeng beng chocolate drink\b",
        r"\bchocolate drink\b",
        r"\bminuman cokelat\b",
        r"\bcocoa drink\b",
        r"\bminuman serbuk\b",
        r"\bdrink powder\b",
        r"\bkopi instant\b",
        r"\bkopi instan\b",
        r"\binstant coffee\b",
        r"\bcoffee sachet\b",
        r"\bkopi bubuk\b",
        r"\bfresco kopi\b",
        r"\btorabika\b",
        r"\bkapal api\b",
        r"\bindoculinaire kopi\b",
        r"\bkopi gula\b",
        r"\blasegar\b",
        r"\bkino lrtn\b",
        r"\bkino larutan\b",
    )):
        if _matches(p, (
            r"\bkopi instant\b", r"\bkopi instan\b",
            r"\binstant coffee\b", r"\bcoffee sachet\b",
            r"\bkopi bubuk\b", r"\bfresco kopi\b",
            r"\btorabika\b", r"\bkapal api\b",
            r"\bindoculinaire kopi\b", r"\bkopi gula\b",
        )):
            return ("Minuman & Produk Susu", "Kopi")
        if _matches(p, (
            r"\bbeng beng chocolate drink\b",
            r"\bchocolate drink\b",
            r"\bminuman cokelat\b",
            r"\bcocoa drink\b",
            r"\bminuman serbuk\b",
            r"\bdrink powder\b",
        )):
            return ("Minuman & Produk Susu", "Minuman serbuk/cokelat")
        if _matches(p, (r"\bcimory eat milk\b",)):
            return ("Minuman & Produk Susu", "Susu/produk susu")
        return ("Minuman & Produk Susu", "Minuman herbal/penyegar")

    # Distinguish coffee scented household goods and candy from actual coffee.
    if _matches(p, (
        r"\bkopiko\b.*\b(candy|permen)\b",
        r"\bkopi\b.*\b(candy|permen|jelly|pudding)\b",
        r"\bpermen kopi\b",
        r"\bkopiko candy\b",
    )):
        return ("Snack & Makanan Kemasan", "Cokelat/permen/snack kemasan")

    # The Sukses's family is instant noodles even if the source does not
    # explicitly retain the words "mi instan" in its product title.
    if (
        _matches(p, (r"\bsukses\b", r"\bsuksess\b"))
        and _matches(p, (r"\bmie goreng\b", r"\bmi goreng\b"))
    ):
        return ("Snack & Makanan Kemasan", "Mi instan")

    # Named instant-noodle and packaged-biscuit products outrank incidental
    # seasoning words (e.g. kaldu, goreng, peanut butter, chocolate).
    if _matches(p, (
        r"\bsukses.?s?\b.*\bmie goreng\b",
        r"\bsuksess\b.*\bmi instan\b",
        r"\bsuksess\b.*\bmie instan\b",
        r"\broma sari gandum\b",
        r"\bsandwichi\b",
        r"\bsandwich biscuit\b",
        r"\bbiscuit sandwich\b",
        r"\bnissin biscuit\b",
        r"\bserena biscuit\b",
    )):
        return ("Snack & Makanan Kemasan", "Snack/makanan kemasan")

    # Explicit snack product forms are checked before broad words such as
    # "susu", "tea", and "water" used as flavours/variant names.
    # Explicit snack/packaged-food forms, not ingredient categories.
    if _matches(p, (
        r"\bindomie\b",
        r"\bmie instan\b",
        r"\bmi instan\b",
        r"\bmie sedaap\b",
        r"\bpop mie\b",
    )):
        return ("Snack & Makanan Kemasan", "Mi instan")

    if _matches(p, (
        r"\bcheetos\b",
        r"\bkuaci\b",
        r"\brebo kuaci\b",
        r"\bsnack\b",
        r"\bchips\b",
        r"\bkeripik\b",
        r"\bwafer\b",
        r"\bbiskuit\b",
        r"\bbiscuit\b",
        r"\bcookies?\b",
        r"\bchocolate candy\b",
        r"\bcandy\b",
        r"\bpermen\b",
        r"\bhello panda\b",
        r"\bselamat wafer\b",
        r"\bmi instan\b",
        r"\bmie instan\b",
        r"\bindomie\b",
        r"\bmie sedaap\b",
        r"\bpop mie\b",
        r"\bpopcorn\b",
        r"\bceres choco spread\b",
        r"\bchoco spread\b",
        r"\bbeng beng\b",
    )):
        # Drink SKUs were already handled above, so a snack brand name
        # that includes a flavour term remains in the packaged-food category.
        if _matches(p, (
            r"\bcheetos\b", r"\bsnack\b", r"\bchips\b",
            r"\bkeripik\b", r"\bwafer\b", r"\bbiskuit\b",
            r"\bbiscuit\b", r"\bcookies?\b", r"\bcandy\b",
            r"\bpermen\b", r"\bhello panda\b", r"\bselamat wafer\b",
            r"\bmi instan\b", r"\bmie instan\b", r"\bindomie\b",
            r"\bmie sedaap\b", r"\bpop mie\b", r"\bpopcorn\b",
            r"\bceres choco spread\b", r"\bchoco spread\b",
        )):
            return ("Snack & Makanan Kemasan", "Snack/makanan kemasan")
        return ("Snack & Makanan Kemasan", "Makanan kemasan")


    # Explicit beverage types and formats beat fruit-flavour words.
    if _matches(p, (
        r"\bair mineral\b",
        r"\bair minum\b",
        r"\bal kaline water\b",
        r"\balkaline water\b",
        r"\bsoft drink\b",
        r"\bminuman\b",
        r"\bdrink\b",
        r"\bwater\b",
        r"\bjuice\b",
        r"\bjus\b",
        r"\blarutan\b",
        r"\bsoda\b",
        r"\bcola\b",
        r"\bteh\b",
        r"\btea\b",
        r"\byogh?urt\b",
        r"\bsusu\b",
        r"\buht\b",
        r"\bgreen sands\b",
        r"\bvio 8\b",
        r"\bpristine\b",
        r"\bcleo\b",
        r"\ble minerale\b",
        r"\bcoca cola\b",
        r"\bfanta\b",
        r"\bsprite\b",
        r"\bfrestea\b",
        r"\bfres tea\b",
        r"\bkopi susu\b",
        r"\blatte\b",
        r"\bcappuccino\b",
        r"\bes kopi\b",
    )):
        if _matches(p, (r"\bkopi susu\b", r"\blatte\b", r"\bcappuccino\b", r"\bes kopi\b")):
            return ("Minuman & Produk Susu", "Kopi/minuman kopi")
        if _matches(p, (r"\byogh?urt\b", r"\bsusu\b", r"\buht\b")):
            return ("Minuman & Produk Susu", "Susu/produk susu")
        if _matches(p, (r"\bteh\b", r"\btea\b", r"\bfrestea\b", r"\bfres tea\b")):
            return ("Minuman & Produk Susu", "Teh/minuman teh")
        if _matches(p, (r"\blarutan\b", r"\blasegar\b", r"\bkino lrtn\b", r"\bkino larutan\b")):
            return ("Minuman & Produk Susu", "Minuman herbal/penyegar")
        if _matches(p, (r"\bminuman cokelat\b", r"\bchocolate drink\b")):
            return ("Minuman & Produk Susu", "Minuman serbuk/cokelat")
        if _matches(p, (r"\bair mineral\b", r"\bair minum\b", r"\bwater\b", r"\bvio 8\b", r"\bpristine\b", r"\bcleo\b", r"\ble minerale\b", r"\balkaline water\b")):
            return ("Minuman & Produk Susu", "Air mineral/minuman kemasan")
        return ("Minuman & Produk Susu", "Minuman ringan/kemasan")

    # Staple/kitchen ingredients stay apart from snacks and ready-to-eat items.
    if _matches(p, (
        r"\bsaus\b",
        r"\bsambal\b",
        r"\bkecap\b",
        r"\bbumbu\b",
        r"\bspice mix\b",
        r"\bmayonnaise\b",
        r"\bmayones\b",
        r"\bminyak goreng\b",
        r"\bcooking oil\b",
        r"\bberas\b",
        r"\bgula pasir\b",
        r"\bgula aren\b",
        r"\btepung\b",
        r"\btelur\b",
        r"\bmargarin\b",
        r"\bmentega\b",
        r"\bsantan\b",
        r"\bkaldu\b",
        r"\bsarden\b",
        r"\bsardines?\b",
        r"\bsaus tiram\b",
        r"\blee kum kee\b",
    )):
        return ("Makanan & Bahan Pokok", "Bahan pokok/saus/bumbu")

    # Fresh fruit/produce only when the product itself is named as produce.
    # Do not infer fruit from flavour names like "jeruk nipis" in detergents,
    # drinks, medicines, shampoo or cosmetics.
    if _matches(p, (
        r"^(?:buah\s+)?jeruk(?:\s+(?:medan|mandarin|ponkan|pontianak|murcott|murkot|lokal|segar))?(?:\s+\d.*)?$",
        r"^jeruk\s+murkot(?:t)?\b",
        r"^(?:buah\s+)?apel(?:\s+(?:malang|fuji|segar|lokal))?(?:\s+\d.*)?$",
        r"^(?:buah\s+)?alpukat(?:\s+\d.*)?$",
        r"^(?:buah\s+)?pisang(?:\s+\d.*)?$",
        r"^(?:buah\s+)?anggur(?:\s+\d.*)?$",
        r"^(?:buah\s+)?semangka(?:\s+\d.*)?$",
        r"^(?:buah\s+)?pepaya(?:\s+\d.*)?$",
        r"^(?:buah\s+)?melon(?:\s+\d.*)?$",
        r"^(?:sayur|sayuran|wortel|kangkung|bayam segar|tomat segar)(?:\s+\d.*)?$",
    )):
        return ("Produk Segar & Beku", "Buah/sayur segar")

    # Product cues for actual fashion items only; "hijab" alone is not enough
    # when the named item is clearly a shampoo, body-care or laundry SKU.
    if _matches(p, (
        r"\bhijab segi empat\b",
        r"\bkerudung\b",
        r"\bjilbab\b",
        r"\bmukena\b",
        r"\bbergo\b",
        r"\bpashmina\b",
        r"\bkhimar\b",
    )) and not _matches(p, (
        r"\bshampoo\b", r"\bsampo\b", r"\bdetergent\b",
        r"\bdeterjen\b", r"\bdetergen\b", r"\bsabun\b",
        r"\bbody wash\b", r"\bvitamin\b", r"\bobat\b",
    )):
        return ("Fashion & Aksesori", "Hijab/kerudung")

    return None


def _category_product_text(row: dict | None) -> str:
    """Prefer a richer original SKU when the normalized name was truncated.

    This is a deliberately narrow recovery: use the original only when the
    normalized label is at most two tokens and the original has a clear size or
    pack quantity absent from the short label. Longer noisy editorial text is
    not restored by this rule.
    """
    row = row or {}
    primary = _norm(row.get("product_text") or row.get("Nama Produk"))
    original = _norm(row.get("original_product_text") or row.get("product_name_variants"))
    if not primary:
        return original
    primary_tokens = re.findall(r"[a-z0-9]+", primary)
    original_tokens = re.findall(r"[a-z0-9]+", original)
    quantity = re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|mg|mcg|pcs?|pack|sachet|tablet|kaplet|botol|kaleng|gr|gram|liter|meter|cm|mm|inch|inci|sheets?|lembar|rolls?)\b|"
        r"\b\d+\s*[x×]\s*\d+\b",
        re.I,
    )
    if (
        len(primary_tokens) <= 2
        and len(original_tokens) >= 4
        and quantity.search(original)
        and not quantity.search(primary)
    ):
        return original
    return primary


def _classify_promo_category_unmapped(row: dict | None) -> tuple[str, str]:
    """Return (Kategori Produk, Subkategori) from product evidence and context.

    Product name cues have priority. Campaign-only headings are separated into
    the dedicated general-promo category. Weak/ambiguous text goes to review.
    """
    row = row or {}
    product = _category_product_text(row)
    offer = _norm(row.get("offer_text") or row.get("Detail Penawaran"))
    title = _norm(row.get("campaign_title") or row.get("Kampanye") or row.get("title"))
    merchant = _norm(row.get("merchant_name") or row.get("Merchant"))
    section = _norm(row.get("section"))
    context = " ".join(part for part in (title, merchant, section, offer) if part)
    product_and_offer = " ".join(part for part in (product, offer) if part)

    if product and _is_non_product_phrase(product):
        return REVIEW, "Teks FAQ/deskripsi non-produk"

    if not product:
        if _matches(context, (r"\b(?:rangkuman|kumpulan|beberapa|berbagai|\d+\s+promo|multi[- ]merchant|multi[- ]kategori)\b",)):
            return GENERAL, _generic_campaign_subcategory(context)
        if _matches(context, (r"\b(hokben|mcdonald|mcd|kfc|burger king|subway|wingstop|bakmi gm|kopi kenangan|starbucks|j\.co|gokana|marugame|yoshinoya|pizza hut|phd|fore coffee|janji jiwa|richeese|genki sushi|chatime|mixue|burger bangor|point coffee|solaria|es teler 77|paris baguette|popeyes|a w)\b",)) and _matches(context, (r"\b(menu|paket|makanan|minuman|kopi|promo|diskon|bundling|beli|gratis|weekend|restoran|kafe|cafe|fries|chicken|ice cone|float|chillz)\b",)):
            return "Restoran & Makanan Siap Saji", "Promo menu/restoran"
        if _matches(context, (r"\bcashback\b", r"\bqris\b", r"\bkartu kredit\b", r"\bpaylater\b", r"\bpromo bank\b")):
            return "Pembayaran, Cashback & Loyalitas", "Cashback/pembayaran"
        if _matches(context, (r"\bvoucher\b", r"\bkupon\b", r"\bgift card\b")):
            return "Voucher & Gift Card", "Voucher/kupon"
        if _matches(context, (r"\btiket pesawat\b", r"\bhotel\b", r"\btraveloka\b", r"\btiket kereta\b")):
            return "Perjalanan & Transportasi", "Tiket/hotel/transportasi"
        if _matches(context, (r"\bbioskop\b", r"\bkonser\b", r"\btaman rekreasi\b", r"\btiket acara\b")):
            return "Hiburan & Rekreasi", "Tiket hiburan"
        if _matches(context, (r"\bpulsa\b", r"\bpaket data\b", r"\bpaket internet\b", r"\bnetflix\b", r"\bspotify\b")):
            return "Telekomunikasi & Digital", "Layanan digital"
        if _matches(context, (r"\bpromo\b", r"\bkatalog\b", r"\bdiskon\b", r"\bpenawaran\b")):
            return GENERAL, _generic_campaign_subcategory(context)
        return REVIEW, "Produk/promo belum jelas"

    # High-confidence item-type rules override broad or incidental keywords.
    strict = _strict_product_override(product)
    if strict:
        return strict

    # Fine-grained item signals produce useful subcategory filters.
    specific = _match_rules(product, SUBCATEGORY_RULES)
    if specific:
        return specific

    # Explicit digital/payment/service terms in product text are promotional
    # products, not retail SKUs. Give the campaign its own suitable category.
    non_product = _match_rules(product, RULES)
    if non_product:
        return non_product

    # Food-specific cues should not let generic campaign words outrank a SKU.
    food_match = _match_rules(product, FOOD_RULES)
    if food_match:
        category, subcategory = food_match
        # Packaged coffee/tea sold in a grocery context remains a grocery beverage.
        # Menu/paket cues and recognized food-service brands route to restaurant.
        if category in {"Snack & Makanan Kemasan", "Restoran & Makanan Siap Saji"}:
            return category, subcategory
        if category == "Minuman & Produk Susu" and _matches(product + " " + merchant + " " + title, (
            r"\b(menu|paket hemat|paket makanan|set menu|combo meal|dine in|take away)\b",
            r"\b(hokben|mcdonald|mcd|kfc|burger king|subway|wingstop|bakmi gm|kopi kenangan|starbucks|j\.co|gokana|marugame|yoshinoya|pizza hut|phd|fore coffee|janji jiwa|richeese|genki sushi|chatime|mixue|burger bangor|point coffee|solaria|es teler 77|paris baguette|popeyes|a w)\b",
        )):
            return "Restoran & Makanan Siap Saji", "Menu/minuman siap saji"
        return category, subcategory

    # If the text is a campaign or catalog heading rather than a product, don't
    # assign it to a guessed SKU category even if the campaign describes a store.
    if _matches(product, GENERAL_CAMPAIGN_PATTERNS) or (
        re.match(r"^promo\b", product)
        and not _matches(product, (r"\b(?:shampoo|sampo|sabun mandi|deterjen|detergen|susu|kopi|teh|biskuit|keripik|indomie|iphone|laptop|popok|voucher|cashback|tiket|menu|burger|sandwich|sushi|pizza|pasta|roti|daging|buah|beras|minyak goreng|vitamin|pasta gigi)\b",))
    ):
        combined_context = " ".join([product, title, merchant, offer])
        return GENERAL, _generic_campaign_subcategory(combined_context)

    # Context fallback is intentionally conservative: it is used only when the
    # product's text itself does not carry a recognizable item/category signal.
    dining_context = " ".join([title, merchant, section, product, offer])
    if _matches(dining_context, (r"\b(hokben|mcdonald|mcd|kfc|burger king|subway|wingstop|bakmi gm|kopi kenangan|starbucks|j\.co|gokana|marugame|yoshinoya|pizza hut|phd|fore coffee|janji jiwa|richeese|genki sushi|chatime|mixue|burger bangor|point coffee|solaria|es teler 77|paris baguette|popeyes|a w)\b",)) and _matches(dining_context, (r"\b(menu|paket|makanan|minuman|kopi|promo|diskon|bundling|beli|gratis|weekend|restoran|kafe|cafe|fries|chicken|ice cone|float|chillz)\b",)):
        return "Restoran & Makanan Siap Saji", "Promo menu/restoran"

    # An unclear one-word "product" must not inherit its category from a
    # payment/campaign note. Keep it in Review unless the SKU itself matched.
    if len(product.split()) <= 1:
        return REVIEW, "Produk belum teridentifikasi"

    # Catch offer-only items without overwriting an identifiable product.
    if _matches(product_and_offer, (r"\bcashback\b", r"\bcash back\b", r"\bqris\b", r"\bkartu kredit\b", r"\bpaylater\b", r"\bcicilan\b", r"\bpromo bank\b")):
        return "Pembayaran, Cashback & Loyalitas", "Cashback/pembayaran"
    if _matches(product_and_offer, (r"\bvoucher\b", r"\bkupon\b", r"\bgift card\b")):
        return "Voucher & Gift Card", "Voucher/kupon"
    if _matches(product_and_offer, (r"\bgratis ongkir\b", r"\bvoucher ongkir\b", r"\bmarketplace\b", r"\bshopee\b", r"\btokopedia\b", r"\bblibli\b", r"\blazada\b", r"\btiktok shop\b")):
        return "Belanja Online & Marketplace", "Marketplace/ongkir"

    # A row with little identity and broad campaign language is not a SKU.
    if _matches(product, (r"\bpromo\b", r"\bkatalog\b", r"\bdiskon\b", r"\bpenawaran\b", r"\bminimum transaksi\b", r"\bserba gratis\b", r"\bberhadiah\b")):
        return GENERAL, _generic_campaign_subcategory(product + " " + title)

    return REVIEW, "Produk belum teridentifikasi"


def _canonical_category_pair(row: dict | None, pair: tuple[str, str]) -> tuple[str, str]:
    """Refine subcategories and reject labels outside the 29-category taxonomy."""
    category, subcategory = pair
    row = row or {}
    product = _category_product_text(row)

    # Active rules emit final taxonomy labels directly; this function only
    # applies subcategory refinements and rejects labels outside the taxonomy.

    # Refine broad subcategories where the SKU text carries an exact item type.
    # These refinements affect labels only, never price, date, or verification.
    if category == "Makanan & Bahan Pokok":
        if re.search(r"\bberas\b", product):
            subcategory = "Beras"
        elif re.search(r"\b(minyak goreng|cooking oil)\b", product):
            subcategory = "Minyak goreng"
        elif re.search(r"\b(gula pasir|gula aren|gula)\b", product):
            subcategory = "Gula"
        elif re.search(r"\btepung\b", product):
            subcategory = "Tepung"
        elif re.search(r"\btelur\b", product):
            subcategory = "Telur"
        elif re.search(r"\b(saus|sambal|kecap|bumbu|mayonnaise|mayones|kaldu)\b", product):
            subcategory = "Saus/sambal/bumbu"

    if category == "Perawatan Tubuh & Mandi":
        if re.search(r"\b(sabun mandi|body wash|shower gel)\b", product):
            subcategory = "Sabun mandi/body wash"
        elif re.search(r"\b(body lotion|lotion|body serum|serum tubuh)\b", product):
            subcategory = "Body lotion/serum"
        elif re.search(r"\b(deodorant|deodoran|antiperspirant)\b", product):
            subcategory = "Deodorant/antiperspirant"

    if category == "Perawatan Rambut & Kulit":
        if re.search(r"\b(shampoo|sampo)\b", product):
            subcategory = "Sampo"
        elif re.search(r"\b(conditioner|kondisioner)\b", product):
            subcategory = "Kondisioner"
        elif re.search(r"\b(hair serum|hair mask|tonik rambut)\b", product):
            subcategory = "Perawatan rambut"

    if category == "Kesehatan & Farmasi" and (
        re.search(
            r"\b(vitamin|suplemen|multivitamin|obat|paracetamol|parasetamol|plester|p3k|termometer|tensimeter)\b",
            product,
        )
        or "vitamin/" in _norm(subcategory)
        or "suplemen" in _norm(subcategory)
    ):
        subcategory = "Vitamin/alat kesehatan/obat bebas"

    # Prevent a classifier rule from emitting a category not listed in the
    # reference sheet. Such values should be reviewed, not silently accepted.
    if category not in CATEGORY_NAMES:
        return REVIEW, "Kategori belum terdaftar; perlu review"

    return category, subcategory


def classify_promo_category(row: dict | None) -> tuple[str, str]:
    """Public classifier returning names present in the workbook taxonomy."""
    pair = _classify_promo_category_unmapped(row)
    return _canonical_category_pair(row, pair)

