"""Canonical English display names for supported language classes (#1314).

Every language value Mechanical Librarian writes is the display name of its
ADR-0065 equivalence class: the favoured ISO 639 base, followed by its script
in parentheses when the class has one, as in "English" or "Chinese
(Simplified)". Region is never part of a class, so it is never part of a name.

The tables are fixed and reviewed by hand. They were generated once from the
CLDR English names (the ones the review frontend shows through
`Intl.DisplayNames`), falling back to the first ISO 639-2 or ISO 15924 English
name where CLDR has none, and four names CLDR shares between two bases (`bih`,
`fat`, `tl`, `tw`) use their ISO 639-2 name so that no two classes share a
name. `gaa` is "Ga language" so that its name never reads as the `ga` code. A
script with no English name in either source gives its classes no display
name; such a class keeps the representation its evidence admitted.

The names are also accepted, without regard to case, as representations of
their class wherever a language is read, so a written name reads back as the
class it was written for.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

_BASE_NAMES_TEXT = """
aa: Afar
ab: Abkhazian
ace: Acehnese
ach: Acoli
ada: Adangme
ady: Adyghe
ae: Avestan
af: Afrikaans
afa: Afro-Asiatic languages
afh: Afrihili
ain: Ainu
ak: Akan
akk: Akkadian
ale: Aleut
alg: Algonquian languages
alt: Southern Altai
am: Amharic
an: Aragonese
ang: Old English
anp: Angika
apa: Apache languages
ar: Arabic
arc: Aramaic
arn: Mapuche
arp: Arapaho
art: Artificial languages
arw: Arawak
as: Assamese
ast: Asturian
ath: Athapascan languages
aus: Australian languages
av: Avaric
awa: Awadhi
ay: Aymara
az: Azerbaijani
ba: Bashkir
bad: Banda languages
bai: Bamileke languages
bal: Baluchi
ban: Balinese
bas: Basaa
bat: Baltic languages
be: Belarusian
bej: Beja
bem: Bemba
ber: Berber languages
bg: Bulgarian
bho: Bhojpuri
bi: Bislama
bih: Bihari languages
bik: Bikol
bin: Bini
bla: Siksiká
bm: Bambara
bn: Bangla
bnt: Bantu languages
bo: Tibetan
br: Breton
bra: Braj
bs: Bosnian
btk: Batak languages
bua: Buriat
bug: Buginese
byn: Blin
ca: Catalan
cad: Caddo
cai: Central American Indian languages
car: Carib
cau: Caucasian languages
ce: Chechen
ceb: Cebuano
cel: Celtic languages
ch: Chamorro
chb: Chibcha
chg: Chagatai
chk: Chuukese
chm: Mari
chn: Chinook Jargon
cho: Choctaw
chp: Chipewyan
chr: Cherokee
chy: Cheyenne
cmc: Chamic languages
cnr: Montenegrin
co: Corsican
cop: Coptic
cpe: Creoles and pidgins, English based
cpf: Creoles and pidgins, French-based
cpp: Creoles and pidgins, Portuguese-based
cr: Cree
crh: Crimean Tatar
crp: Creoles and pidgins
cs: Czech
csb: Kashubian
cu: Church Slavic
cus: Cushitic languages
cv: Chuvash
cy: Welsh
da: Danish
dak: Dakota
dar: Dargwa
day: Land Dayak languages
de: German
del: Delaware
den: Slave
dgr: Dogrib
din: Dinka
doi: Dogri
dra: Dravidian languages
dsb: Lower Sorbian
dua: Duala
dum: Middle Dutch
dv: Divehi
dyu: Dyula
dz: Dzongkha
ee: Ewe
efi: Efik
egy: Ancient Egyptian
eka: Ekajuk
el: Greek
elx: Elamite
en: English
enm: Middle English
eo: Esperanto
es: Spanish
et: Estonian
eu: Basque
ewo: Ewondo
fa: Persian
fan: Fang
fat: Fanti
ff: Fula
fi: Finnish
fil: Filipino
fiu: Finno-Ugrian languages
fj: Fijian
fo: Faroese
fon: Fon
fr: French
frm: Middle French
fro: Old French
frr: Northern Frisian
frs: Eastern Frisian
fur: Friulian
fy: Western Frisian
ga: Irish
gaa: Ga language
gay: Gayo
gba: Gbaya
gd: Scottish Gaelic
gem: Germanic languages
gez: Geez
gil: Gilbertese
gl: Galician
gmh: Middle High German
gn: Guarani
goh: Old High German
gon: Gondi
gor: Gorontalo
got: Gothic
grb: Grebo
grc: Ancient Greek
gsw: Swiss German
gu: Gujarati
gv: Manx
gwi: Gwichʼin
ha: Hausa
hai: Haida
haw: Hawaiian
he: Hebrew
hi: Hindi
hil: Hiligaynon
him: Himachali languages
hit: Hittite
hmn: Hmong
ho: Hiri Motu
hr: Croatian
hsb: Upper Sorbian
ht: Haitian Creole
hu: Hungarian
hup: Hupa
hy: Armenian
hz: Herero
ia: Interlingua
iba: Iban
id: Indonesian
ie: Interlingue
ig: Igbo
ii: Sichuan Yi
ijo: Ijo languages
ik: Inupiaq
ilo: Iloko
inc: Indic languages
ine: Indo-European languages
inh: Ingush
io: Ido
ira: Iranian languages
iro: Iroquoian languages
is: Icelandic
it: Italian
iu: Inuktitut
ja: Japanese
jbo: Lojban
jpr: Judeo-Persian
jrb: Judeo-Arabic
jv: Javanese
ka: Georgian
kaa: Kara-Kalpak
kab: Kabyle
kac: Kachin
kam: Kamba
kar: Karen languages
kaw: Kawi
kbd: Kabardian
kg: Kongo
kha: Khasi
khi: Khoisan languages
kho: Khotanese
ki: Kikuyu
kj: Kuanyama
kk: Kazakh
kl: Kalaallisut
km: Khmer
kmb: Kimbundu
kn: Kannada
ko: Korean
kok: Konkani
kos: Kosraean
kpe: Kpelle
kr: Kanuri
krc: Karachay-Balkar
krl: Karelian
kro: Kru languages
kru: Kurukh
ks: Kashmiri
ku: Kurdish
kum: Kumyk
kut: Kutenai
kv: Komi
kw: Cornish
ky: Kyrgyz
la: Latin
lad: Ladino
lah: Western Panjabi
lam: Lamba
lb: Luxembourgish
lez: Lezghian
lg: Ganda
li: Limburgish
ln: Lingala
lo: Lao
lol: Mongo
loz: Lozi
lt: Lithuanian
lu: Luba-Katanga
lua: Luba-Lulua
lui: Luiseno
lun: Lunda
luo: Luo
lus: Mizo
lv: Latvian
mad: Madurese
mag: Magahi
mai: Maithili
mak: Makasar
man: Mandingo
map: Austronesian languages
mas: Masai
mdf: Moksha
mdr: Mandar
men: Mende
mg: Malagasy
mga: Middle Irish
mh: Marshallese
mi: Māori
mic: Mi'kmaw
min: Minangkabau
mis: Uncoded languages
mk: Macedonian
mkh: Mon-Khmer languages
ml: Malayalam
mn: Mongolian
mnc: Manchu
mni: Manipuri
mno: Manobo languages
moh: Mohawk
mos: Mossi
mr: Marathi
ms: Malay
mt: Maltese
mul: Multiple languages
mun: Munda languages
mus: Muscogee
mwl: Mirandese
mwr: Marwari
my: Burmese
myn: Mayan languages
myv: Erzya
na: Nauru
nah: Nahuatl languages
nai: North American Indian languages
nap: Neapolitan
nb: Norwegian Bokmål
nd: North Ndebele
nds: Low German
ne: Nepali
new: Newari
ng: Ndonga
nia: Nias
nic: Niger-Kordofanian languages
niu: Niuean
nl: Dutch
nn: Norwegian Nynorsk
no: Norwegian
nog: Nogai
non: Old Norse
nqo: N’Ko
nr: South Ndebele
nso: Northern Sotho
nub: Nubian languages
nv: Navajo
nwc: Classical Newari
ny: Nyanja
nym: Nyamwezi
nyn: Nyankole
nyo: Nyoro
nzi: Nzima
oc: Occitan
oj: Ojibwa
om: Oromo
or: Odia
os: Ossetic
osa: Osage
ota: Ottoman Turkish
oto: Otomian languages
pa: Punjabi
paa: Papuan languages
pag: Pangasinan
pal: Pahlavi
pam: Pampanga
pap: Papiamento
pau: Palauan
peo: Old Persian
phi: Philippine languages
phn: Phoenician
pi: Pali
pl: Polish
pon: Pohnpeian
pra: Prakrit languages
pro: Old Provençal
ps: Pashto
pt: Portuguese
qu: Quechua
raj: Rajasthani
rap: Rapanui
rar: Rarotongan
rm: Romansh
rn: Rundi
ro: Romanian
roa: Romance languages
rom: Romany
ru: Russian
rup: Aromanian
rw: Kinyarwanda
sa: Sanskrit
sad: Sandawe
sah: Yakut
sai: South American Indian languages
sal: Salishan languages
sam: Samaritan Aramaic
sas: Sasak
sat: Santali
sc: Sardinian
scn: Sicilian
sco: Scots
sd: Sindhi
se: Northern Sami
sel: Selkup
sem: Semitic languages
sg: Sango
sga: Old Irish
sgn: Sign Languages
shn: Shan
si: Sinhala
sid: Sidamo
sio: Siouan languages
sit: Sino-Tibetan languages
sk: Slovak
sl: Slovenian
sla: Slavic languages
sm: Samoan
sma: Southern Sami
smi: Sami languages
smj: Lule Sami
smn: Inari Sami
sms: Skolt Sami
sn: Shona
snk: Soninke
so: Somali
sog: Sogdien
son: Songhai languages
sq: Albanian
sr: Serbian
srn: Sranan Tongo
srr: Serer
ss: Swati
ssa: Nilo-Saharan languages
st: Southern Sotho
su: Sundanese
suk: Sukuma
sus: Susu
sux: Sumerian
sv: Swedish
sw: Swahili
syc: Classical Syriac
syr: Syriac
ta: Tamil
tai: Tai languages
te: Telugu
tem: Timne
ter: Tereno
tet: Tetum
tg: Tajik
th: Thai
ti: Tigrinya
tig: Tigre
tiv: Tiv
tk: Turkmen
tkl: Tokelau
tl: Tagalog
tlh: Klingon
tli: Tlingit
tmh: Tamashek
tn: Tswana
to: Tongan
tog: Nyasa Tonga
tpi: Tok Pisin
tr: Turkish
ts: Tsonga
tsi: Tsimshian
tt: Tatar
tum: Tumbuka
tup: Tupi languages
tut: Altaic languages
tvl: Tuvalu
tw: Twi
ty: Tahitian
tyv: Tuvinian
udm: Udmurt
ug: Uyghur
uga: Ugaritic
uk: Ukrainian
umb: Umbundu
ur: Urdu
uz: Uzbek
vai: Vai
ve: Venda
vi: Vietnamese
vo: Volapük
vot: Votic
wa: Walloon
wak: Wakashan languages
wal: Wolaytta
war: Waray
was: Washo
wen: Sorbian languages
wo: Wolof
xal: Kalmyk
xh: Xhosa
yao: Yao
yap: Yapese
yi: Yiddish
yo: Yoruba
ypk: Yupik languages
za: Zhuang
zap: Zapotec
zbl: Blissymbols
zen: Zenaga
zgh: Standard Moroccan Tamazight
zh: Chinese
znd: Zande languages
zu: Zulu
zun: Zuni
zxx: No linguistic content
zza: Zaza
"""

_SCRIPT_NAMES_TEXT = """
Adlm: Adlam
Afak: Afaka
Aghb: Caucasian Albanian
Ahom: Ahom
Arab: Arabic
Aran: Nastaliq
Armi: Imperial Aramaic
Armn: Armenian
Avst: Avestan
Bali: Balinese
Bamu: Bamum
Bass: Bassa Vah
Batk: Batak
Beng: Bangla
Bhks: Bhaiksuki
Blis: Blissymbols
Bopo: Bopomofo
Brah: Brahmi
Brai: Braille
Bugi: Buginese
Buhd: Buhid
Cakm: Chakma
Cans: Unified Canadian Aboriginal Syllabics
Cari: Carian
Cham: Cham
Cher: Cherokee
Chrs: Chorasmian
Cirt: Cirth
Copt: Coptic
Cpmn: Cypro-Minoan
Cprt: Cypriot
Cyrl: Cyrillic
Cyrs: Old Church Slavonic Cyrillic
Deva: Devanagari
Diak: Dives Akuru
Dogr: Dogra
Dsrt: Deseret
Dupl: Duployan shorthand
Egyd: Egyptian demotic
Egyh: Egyptian hieratic
Egyp: Egyptian hieroglyphs
Elba: Elbasan
Elym: Elymaic
Ethi: Ethiopic
Gara: Garay
Geok: Georgian Khutsuri
Geor: Georgian
Glag: Glagolitic
Gong: Gunjala Gondi
Gonm: Masaram Gondi
Goth: Gothic
Gran: Grantha
Grek: Greek
Gujr: Gujarati
Gukh: Gurung Khema
Guru: Gurmukhi
Hanb: Han with Bopomofo
Hang: Hangul
Hani: Han
Hano: Hanunoo
Hans: Simplified
Hant: Traditional
Hatr: Hatran
Hebr: Hebrew
Hira: Hiragana
Hluw: Anatolian Hieroglyphs
Hmng: Pahawh Hmong
Hmnp: Nyiakeng Puachue Hmong
Hrkt: Japanese syllabaries
Hung: Old Hungarian
Inds: Indus
Ital: Old Italic
Jamo: Jamo
Java: Javanese
Jpan: Japanese
Jurc: Jurchen
Kali: Kayah Li
Kana: Katakana
Kawi: Kawi
Khar: Kharoshthi
Khmr: Khmer
Khoj: Khojki
Kitl: Khitan large script
Kits: Khitan small script
Knda: Kannada
Kore: Korean
Kpel: Kpelle
Krai: Kirat Rai
Kthi: Kaithi
Lana: Lanna
Laoo: Lao
Latf: Fraktur Latin
Latg: Gaelic Latin
Latn: Latin
Leke: Leke
Lepc: Lepcha
Limb: Limbu
Lina: Linear A
Linb: Linear B
Lisu: Fraser
Loma: Loma
Lyci: Lycian
Lydi: Lydian
Mahj: Mahajani
Maka: Makasar
Mand: Mandaean
Mani: Manichaean
Marc: Marchen
Maya: Mayan hieroglyphs
Medf: Medefaidrin
Mend: Mende
Merc: Meroitic Cursive
Mero: Meroitic
Mlym: Malayalam
Modi: Modi
Mong: Mongolian
Moon: Moon
Mroo: Mro
Mtei: Meitei Mayek
Mult: Multani
Mymr: Myanmar
Nagm: Nag Mundari
Nand: Nandinagari
Narb: Old North Arabian
Nbat: Nabataean
Newa: Newa
Nkgb: Naxi Geba
Nkoo: N’Ko
Nshu: Nüshu
Ogam: Ogham
Olck: Ol Chiki
Onao: Ol Onal
Orkh: Orkhon
Orya: Odia
Osge: Osage
Osma: Osmanya
Ougr: Old Uyghur
Palm: Palmyrene
Pauc: Pau Cin Hau
Perm: Old Permic
Phag: Phags-pa
Phli: Inscriptional Pahlavi
Phlp: Psalter Pahlavi
Phlv: Book Pahlavi
Phnx: Phoenician
Piqd: Klingon pIqaD
Plrd: Pollard Phonetic
Prti: Inscriptional Parthian
Rjng: Rejang
Rohg: Hanifi
Roro: Rongorongo
Runr: Runic
Samr: Samaritan
Sara: Sarati
Sarb: Old South Arabian
Saur: Saurashtra
Sgnw: SignWriting
Shaw: Shavian
Shrd: Sharada
Sidd: Siddham
Sind: Khudawadi
Sinh: Sinhala
Sogd: Sogdian
Sogo: Old Sogdian
Sora: Sora Sompeng
Soyo: Soyombo
Sund: Sundanese
Sunu: Sunuwar
Sylo: Syloti Nagri
Syrc: Syriac
Syre: Estrangelo Syriac
Syrj: Western Syriac
Syrn: Eastern Syriac
Tagb: Tagbanwa
Takr: Takri
Tale: Tai Le
Talu: New Tai Lue
Taml: Tamil
Tang: Tangut
Tavt: Tai Viet
Telu: Telugu
Teng: Tengwar
Tfng: Tifinagh
Tglg: Tagalog
Thaa: Thaana
Thai: Thai
Tibt: Tibetan
Tirh: Tirhuta
Tnsa: Tangsa
Todr: Todhri
Toto: Toto
Tutg: Tulu-Tigalari
Ugar: Ugaritic
Vaii: Vai
Visp: Visible Speech
Vith: Vithkuqi
Wara: Varang Kshiti
Wcho: Wancho
Wole: Woleai
Xpeo: Old Persian
Xsux: Sumero-Akkadian Cuneiform
Yezi: Yezidi
Yiii: Yi
Zanb: Zanabazar Square
Zinh: Inherited
Zmth: Mathematical Notation
Zsye: Emoji
Zsym: Symbols
Zxxx: Unwritten
"""


def _table(text: str) -> Mapping[str, str]:
    return MappingProxyType(
        dict(
            (code, name)
            for line in text.strip().splitlines()
            for code, name in (line.split(": ", 1),)
        )
    )


LANGUAGE_BASE_NAMES: Final[Mapping[str, str]] = _table(_BASE_NAMES_TEXT)
LANGUAGE_SCRIPT_NAMES: Final[Mapping[str, str]] = _table(_SCRIPT_NAMES_TEXT)

_BASES_BY_NAME: Final[Mapping[str, str]] = MappingProxyType(
    {name.casefold(): code for code, name in LANGUAGE_BASE_NAMES.items()}
)
_SCRIPTS_BY_NAME: Final[Mapping[str, str]] = MappingProxyType(
    {name.casefold(): code for code, name in LANGUAGE_SCRIPT_NAMES.items()}
)


def language_display_name(base: str, script: str | None) -> str | None:
    """Name one equivalence class, or None when the table cannot name it."""
    name = LANGUAGE_BASE_NAMES.get(base)
    if name is None or script is None:
        return name
    script_name = LANGUAGE_SCRIPT_NAMES.get(script)
    return None if script_name is None else f"{name} ({script_name})"


def language_name_class(value: str) -> tuple[str, str | None] | None:
    """Read a display name, in any case, back to the class it names."""
    folded = value.casefold()
    base = _BASES_BY_NAME.get(folded)
    if base is not None:
        return base, None
    if not folded.endswith(")") or " (" not in folded:
        return None
    name, script_name = folded[:-1].rsplit(" (", 1)
    base = _BASES_BY_NAME.get(name)
    script = _SCRIPTS_BY_NAME.get(script_name)
    if base is None or script is None:
        return None
    return base, script


__all__ = [
    "LANGUAGE_BASE_NAMES",
    "LANGUAGE_SCRIPT_NAMES",
    "language_display_name",
    "language_name_class",
]
