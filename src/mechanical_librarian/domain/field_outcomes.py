"""Closed field-specific acceptable-outcome policy.

The policy compares decision outcomes only. It never rewrites evidence values,
asserts corporate identity, or creates publisher hierarchy relationships.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType


_LANGUAGE_TAG = re.compile(
    r"^(?P<language>[a-z]{2,3})"
    r"(?:-(?P<script>[A-Z][a-z]{3}))?"
    r"(?:-(?P<region>[A-Z]{2}|[0-9]{3}))?$"
)

# Derived from the Library of Congress ISO 639-2 UTF-8 code list. Each alias
# maps to the favored current code. Bibliographic aliases remain accepted, as
# does deprecated ISO 639-1 `bh`, which LoC replaced with favored `bih`.
_ISO_639_ALIASES_TEXT = """
aar=aa aa=aa abk=ab ab=ab ace=ace ach=ach ada=ada ady=ady afa=afa afh=afh afr=af af=af ain=ain aka=ak ak=ak akk=akk alb=sq sqi=sq sq=sq ale=ale alg=alg alt=alt amh=am am=am ang=ang anp=anp apa=apa ara=ar ar=ar arc=arc arg=an an=an arm=hy hye=hy hy=hy arn=arn arp=arp art=art arw=arw asm=as as=as ast=ast ath=ath aus=aus ava=av av=av ave=ae ae=ae awa=awa aym=ay ay=ay aze=az az=az
bad=bad bai=bai bak=ba ba=ba bal=bal bam=bm bm=bm ban=ban baq=eu eus=eu eu=eu bas=bas bat=bat bej=bej bel=be be=be bem=bem ben=bn bn=bn ber=ber bho=bho bh=bih bih=bih bik=bik bin=bin bis=bi bi=bi bla=bla bnt=bnt bos=bs bs=bs bra=bra bre=br br=br btk=btk bua=bua bug=bug bul=bg bg=bg bur=my mya=my my=my byn=byn
cad=cad cai=cai car=car cat=ca ca=ca cau=cau ceb=ceb cel=cel cha=ch ch=ch chb=chb che=ce ce=ce chg=chg chi=zh zho=zh zh=zh chk=chk chm=chm chn=chn cho=cho chp=chp chr=chr chu=cu cu=cu chv=cv cv=cv chy=chy cmc=cmc cnr=cnr cop=cop cor=kw kw=kw cos=co co=co cpe=cpe cpf=cpf cpp=cpp cre=cr cr=cr crh=crh crp=crp csb=csb cus=cus cze=cs ces=cs cs=cs
dak=dak dan=da da=da dar=dar day=day del=del den=den dgr=dgr din=din div=dv dv=dv doi=doi dra=dra dsb=dsb dua=dua dum=dum dut=nl nld=nl nl=nl dyu=dyu dzo=dz dz=dz efi=efi egy=egy eka=eka elx=elx eng=en en=en enm=enm epo=eo eo=eo est=et et=et ewe=ee ee=ee ewo=ewo
fan=fan fao=fo fo=fo fat=fat fij=fj fj=fj fil=fil fin=fi fi=fi fiu=fiu fon=fon fre=fr fra=fr fr=fr frm=frm fro=fro frr=frr frs=frs fry=fy fy=fy ful=ff ff=ff fur=fur gaa=gaa gay=gay gba=gba gem=gem geo=ka kat=ka ka=ka ger=de deu=de de=de gez=gez gil=gil gla=gd gd=gd gle=ga ga=ga glg=gl gl=gl glv=gv gv=gv gmh=gmh goh=goh gon=gon gor=gor got=got grb=grb grc=grc gre=el ell=el el=el grn=gn gn=gn gsw=gsw guj=gu gu=gu gwi=gwi
hai=hai hat=ht ht=ht hau=ha ha=ha haw=haw heb=he he=he her=hz hz=hz hil=hil him=him hin=hi hi=hi hit=hit hmn=hmn hmo=ho ho=ho hrv=hr hr=hr hsb=hsb hun=hu hu=hu hup=hup iba=iba ibo=ig ig=ig ice=is isl=is is=is ido=io io=io iii=ii ii=ii ijo=ijo iku=iu iu=iu ile=ie ie=ie ilo=ilo ina=ia ia=ia inc=inc ind=id id=id ine=ine inh=inh ipk=ik ik=ik ira=ira iro=iro ita=it it=it
jav=jv jv=jv jbo=jbo jpn=ja ja=ja jpr=jpr jrb=jrb kaa=kaa kab=kab kac=kac kal=kl kl=kl kam=kam kan=kn kn=kn kar=kar kas=ks ks=ks kau=kr kr=kr kaw=kaw kaz=kk kk=kk kbd=kbd kha=kha khi=khi khm=km km=km kho=kho kik=ki ki=ki kin=rw rw=rw kir=ky ky=ky kmb=kmb kok=kok kom=kv kv=kv kon=kg kg=kg kor=ko ko=ko kos=kos kpe=kpe krc=krc krl=krl kro=kro kru=kru kua=kj kj=kj kum=kum kur=ku ku=ku kut=kut
lad=lad lah=lah lam=lam lao=lo lo=lo lat=la la=la lav=lv lv=lv lez=lez lim=li li=li lin=ln ln=ln lit=lt lt=lt lol=lol loz=loz ltz=lb lb=lb lua=lua lub=lu lu=lu lug=lg lg=lg lui=lui lun=lun luo=luo lus=lus mac=mk mkd=mk mk=mk mad=mad mag=mag mah=mh mh=mh mai=mai mak=mak mal=ml ml=ml man=man mao=mi mri=mi mi=mi map=map mar=mr mr=mr mas=mas may=ms msa=ms ms=ms mdf=mdf mdr=mdr men=men mga=mga mic=mic min=min mis=mis mkh=mkh mlg=mg mg=mg mlt=mt mt=mt mnc=mnc mni=mni mno=mno moh=moh mon=mn mn=mn mos=mos mul=mul mun=mun mus=mus mwl=mwl mwr=mwr myn=myn myv=myv
nah=nah nai=nai nap=nap nau=na na=na nav=nv nv=nv nbl=nr nr=nr nde=nd nd=nd ndo=ng ng=ng nds=nds nep=ne ne=ne new=new nia=nia nic=nic niu=niu nno=nn nn=nn nob=nb nb=nb nog=nog non=non nor=no no=no nqo=nqo nso=nso nub=nub nwc=nwc nya=ny ny=ny nym=nym nyn=nyn nyo=nyo nzi=nzi oci=oc oc=oc oji=oj oj=oj ori=or or=or orm=om om=om osa=osa oss=os os=os ota=ota oto=oto
paa=paa pag=pag pal=pal pam=pam pan=pa pa=pa pap=pap pau=pau peo=peo per=fa fas=fa fa=fa phi=phi phn=phn pli=pi pi=pi pol=pl pl=pl pon=pon por=pt pt=pt pra=pra pro=pro pus=ps ps=ps que=qu qu=qu raj=raj rap=rap rar=rar roa=roa roh=rm rm=rm rom=rom rum=ro ron=ro ro=ro run=rn rn=rn rup=rup rus=ru ru=ru
sad=sad sag=sg sg=sg sah=sah sai=sai sal=sal sam=sam san=sa sa=sa sas=sas sat=sat scn=scn sco=sco sel=sel sem=sem sga=sga sgn=sgn shn=shn sid=sid sin=si si=si sio=sio sit=sit sla=sla slo=sk slk=sk sk=sk slv=sl sl=sl sma=sma sme=se se=se smi=smi smj=smj smn=smn smo=sm sm=sm sms=sms sna=sn sn=sn snd=sd sd=sd snk=snk sog=sog som=so so=so son=son sot=st st=st spa=es es=es srd=sc sc=sc srn=srn srp=sr sr=sr srr=srr ssa=ssa ssw=ss ss=ss suk=suk sun=su su=su sus=sus sux=sux swa=sw sw=sw swe=sv sv=sv syc=syc syr=syr
tah=ty ty=ty tai=tai tam=ta ta=ta tat=tt tt=tt tel=te te=te tem=tem ter=ter tet=tet tgk=tg tg=tg tgl=tl tl=tl tha=th th=th tib=bo bod=bo bo=bo tig=tig tir=ti ti=ti tiv=tiv tkl=tkl tlh=tlh tli=tli tmh=tmh tog=tog ton=to to=to tpi=tpi tsi=tsi tsn=tn tn=tn tso=ts ts=ts tuk=tk tk=tk tum=tum tup=tup tur=tr tr=tr tut=tut tvl=tvl twi=tw tw=tw tyv=tyv
udm=udm uga=uga uig=ug ug=ug ukr=uk uk=uk umb=umb und=und urd=ur ur=ur uzb=uz uz=uz vai=vai ven=ve ve=ve vie=vi vi=vi vol=vo vo=vo vot=vot wak=wak wal=wal war=war was=was wel=cy cym=cy cy=cy wen=wen wln=wa wa=wa wol=wo wo=wo xal=xal xho=xh xh=xh yao=yao yap=yap yid=yi yi=yi yor=yo yo=yo ypk=ypk zap=zap zbl=zbl zen=zen zgh=zgh zha=za za=za znd=znd zul=zu zu=zu zun=zun zxx=zxx zza=zza
"""
_ISO_639_ALIASES = MappingProxyType(
    {
        alias: base
        for item in _ISO_639_ALIASES_TEXT.split()
        for alias, base in (item.split("=", 1),)
    }
)

# Current non-deprecated IANA script and region subtags used by the supported
# language shape. Private-use ranges and unknown placeholders are not admitted.
_SCRIPTS = frozenset(
    """
Adlm Afak Aghb Ahom Arab Aran Armi Armn Avst Bali Bamu Bass Batk Beng Berf Bhks Blis Bopo Brah Brai Bugi Buhd Cakm Cans Cari Cham Cher Chis Chrs Cirt Copt Cpmn Cprt Cyrl Cyrs Deva Diak Dogr Dsrt Dupl Egyd Egyh Egyp Elba Elym Ethi Gara Geok Geor Glag Gong Gonm Goth Gran Grek Gujr Gukh Guru Hanb Hang Hani Hano Hans Hant Hatr Hebr Hira Hluw Hmng Hmnp Hntl Hrkt Hung Inds Ital Jamo Java Jpan Jurc Kali Kana Kawi Khar Khmr Khoj Kitl Kits Knda Kore Kpel Krai Kthi Lana Laoo Latf Latg Latn Leke Lepc Limb Lina Linb Lisu Loma Lyci Lydi Mahj Maka Mand Mani Marc Maya Medf Mend Merc Mero Mlym Modi Mong Moon Mroo Mtei Mult Mymr Nagm Nand Narb Nbat Newa Nkdb Nkgb Nkoo Nshu Ogam Olck Onao Orkh Orya Osge Osma Ougr Palm Pauc Pcun Pelm Perm Phag Phli Phlp Phlv Phnx Piqd Plrd Prti Psin Ranj Rjng Rohg Roro Runr Samr Sara Sarb Saur Seal Sgnw Shaw Shrd Shui Sidd Sidt Sind Sinh Sogd Sogo Sora Soyo Sund Sunu Sylo Syrc Syre Syrj Syrn Tagb Takr Tale Talu Taml Tang Tavt Tayo Telu Teng Tfng Tglg Thaa Thai Tibt Tirh Tnsa Todr Tols Toto Tutg Ugar Vaii Visp Vith Wara Wcho Wole Xpeo Xsux Yezi Yiii Zanb Zinh Zmth Zsye Zsym Zxxx
""".split()
)
_REGIONS = frozenset(
    """
AA AC AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CP CQ CR CU CV CW CX CY CZ DE DG DJ DK DM DO DZ EA EC EE EG EH ER ES ET EU EZ FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU IC ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TA TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM UN US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW 001 002 003 005 009 011 013 014 015 017 018 019 021 029 030 034 035 039 053 054 057 061 142 143 145 150 151 154 155 202 419
""".split()
)
_PUBLISHER_PUNCTUATION = frozenset("&'’.,:;()+-/–—")


@dataclass(frozen=True, slots=True)
class LanguageIdentity:
    """Decision identity for one canonical supported language tag."""

    base: str
    script: str | None
    region: str | None


def language_identity(value: str) -> LanguageIdentity | None:
    """Parse the closed canonical ISO/BCP 47 subset used by proposal policy."""
    match = _LANGUAGE_TAG.fullmatch(value)
    if match is None:
        return None
    base = _ISO_639_ALIASES.get(match["language"])
    script = match["script"]
    region = match["region"]
    if (
        base is None
        or (script is not None and script not in _SCRIPTS)
        or (region is not None and region not in _REGIONS)
    ):
        return None
    return LanguageIdentity(base, script, region)


def language_outcomes_compatible(left: str, right: str) -> bool:
    """Return whether two tags describe one non-contradictory language outcome."""
    left_identity = language_identity(left)
    right_identity = language_identity(right)
    if left_identity is None or right_identity is None:
        return False
    return (
        left_identity.base == right_identity.base
        and (
            left_identity.script is None
            or right_identity.script is None
            or left_identity.script == right_identity.script
        )
        and (
            left_identity.region is None
            or right_identity.region is None
            or left_identity.region == right_identity.region
        )
    )


def publisher_statement_valid(value: str) -> bool:
    """Validate a broad publisher/imprint/brand/group statement syntax."""
    return (
        bool(value)
        and value == value.strip()
        and value == unicodedata.normalize("NFC", value)
        and "://" not in value
        and any(character.isalnum() for character in value)
        and all(
            character.isalnum()
            or character.isspace()
            or character in _PUBLISHER_PUNCTUATION
            for character in value
        )
    )


__all__ = [
    "LanguageIdentity",
    "language_identity",
    "language_outcomes_compatible",
    "publisher_statement_valid",
]
