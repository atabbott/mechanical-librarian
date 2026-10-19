"""Compact authority records, reviewed source ownership and frozen decoding parity."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts import verify_qualification_records as records

from mechanical_librarian.domain import audiobook_decision_qualification as audio
from mechanical_librarian.domain import automatic_decision as automatic
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
    public_summary,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/qualification-authority-predecessor"
F38_MANIFEST = (
    ROOT / "docs/reference/f38-contributor-display-qualification-manifest-v1.json"
)


def test_current_records_match_runtime_and_frozen_permissions():
    records.verify()
    expected = json.loads((FIXTURE / "historical-permissions.json").read_bytes())
    provenance = json.loads((FIXTURE / "provenance.json").read_bytes())
    for name, digest in provenance["files"].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest
    current = {
        "F19A": automatic.F19A_QUALIFIED_POLICY_OPTIONS,
        "F20": F20_PASSING_AUTOMATIC_OPTIONS,
        "F46": audio.F46_PASSING_AUTOMATIC_OPTIONS,
    }
    assert [len(current[f]) for f in ("F19A", "F20", "F46")] == [12, 13, 76]
    displaced = {
        "F19A": {
            "0345126ad4bea8470e85754c4f0781af298c6ee8953be9f962c26651645ae67e": 2,
            "c5bffe0ae40efa8f0a2ad4a9ebd492a60a7b7626facc8c1f47048dacecda112c": 2,
            "b3178f2f3bb157e06d34e2296f045a51879585406befbe1de3134fd965901e21": 2,
            "65e6642aaf3bf108de535a1e0db6b97513c26cda4fdc6a670e6690f04e41d4cb": 2,
            "df4243cdf60308fe751e98eef91310023585bc2845146741fee24098ab76d640": 2,
            "832fbfdab32630dac9621d67a4a46f9be72dc9e13d8ca6bb71c98325c1a3d95c": 2,
            "6d7c7adc4cdecdc54ef8b6f106ce7a7dc69ab16514a7738d03ff7179bee8bb4d": 2,
            "7504044e5fd309e18ec459fa8f5b6ab79a1eaf4d0375c453cb4cbd773c7aff3d": 2,
            "c56da1ff45f2749da3320c2737e7831247f877e9372996d662854a56a06ae00d": 2,
            "6913ba37ecc710b36a7053a07aefc93dfa81173d88dce5b83bac5cb42db8168c": 2,
            "2c441ef5ff534d16ab2be38cfdd5074c7d705f1418c95d90cc9431153b6fd9dc": 2,
            "4868a47c6c11039a80bb8282e650197feb42d6acf2d8159e4865fd8d95bc1fcf": 2,
            "a8b3c7313c303da3887bfff2c78dc94da9c9fb1479b942965e699455596543b1": 2,
            "a58b6124ab1e3969f736d6be5f334ec4bbde7fbb7d1092d46a0775ed58f74917": 2,
            "4ba6c700429b90200d3b5a199224a5e8516a52ecc485142bac314ea4172c7d4b": 2,
            "38075caf63c051fb2031d6e894bcf7a462cfe5b66399ff8746b74ed5850bd5f1": 2,
            "00b19ce194b1747007f45990fa8412820082160294c21943064185f50bb05add": 2,
            "5dddeb7bf21a0dfd7dabd3dfb6de59e85c705b349c229d9d40e15f0524e82d2e": 2,
            "d2a930fef7ff42cc2eb1ef2bc7ae9a0e804318c39b7b66647180f8f25919d4d9": 2,
            "817f9b8259502c8e51c538f7fc025198e9e9746f7f84f1b4bbbb44e0e7e3dbcd": 2,
            "ba8c43413b9419d41f225c99e81d72b87a3ffb955bff10449dc1fb5676ead0e0": 2,
            "d568b1d59a99ab6cd2e50165a7365742ab317fa2d4a0f76de877a2e185e9ea1d": 2,
            "d9f6e90b2bcb0b020556f7492990ee86727482f7148579443d2d1bf9ac49d6c4": 2,
            "f8dd88e5cf53a6e47871d8f8bf3357331a6b59baa72827b23891944c47d62344": 2,
            "631286528f23707c48a5782c43c108ad7a721c4a10b0c34199246fa68adde51a": 2,
            "35a6b200b77264c7b0ee1393c5594000d2872ff9d11fd8fc25d6b65ca919c024": 2,
            "a8478e315c61c53cbdedeb420fc4c115bc3e440bf879d3590c2865df0bf79c32": 2,
            "c4390328dbd2d39fefc68f68819d37b126974175843de4b3944e020508502dbf": 2,
            "38e51dfa85abd8be8606234ee7c1eb789daa176f863f8d9f701deea3969f1f5b": 2,
            "91c293b69ef7153b63c458b87ce289598ceda8363fabc33621c07e90cf04455f": 2,
            "3f512f587f999ce675672686b03d96213bbd8afe44abdc2b17fe7aa8f2328bd9": 2,
            "b236b2612772f298c44e196a82e053a6b63e8eee05ea4c105bc262bdce0489ab": 2,
            "b0987ca2be0c2171545da8942b1646a99197e1315714e985b07049b637ca63b3": 2,
            "8a443c1c879ec69b64f8eef5ce4905042b570f265669411eda9ee8cddd404187": 2,
            "437a7549d2007132ba228bd828fba0356fd89f46df41a2afbf38eed8a340a052": 2,
            "1469272be676f1c42591d87cf76033b25559082da7411cac0738b716b9c0fcb2": 2,
            "8e075ba6671ac1dee8ed6c5dfc9f7285380397ba47d292d3e174742a9f962147": 2,
            "d48649f16a28bd7cc083cd2c41ef3d5372a5ee050c9e763719fafd86178dcfb2": 2,
            "a46d75a576f05ed37c3d9b23b994d349968ab1e6bab52df27aa60480462df1ae": 2,
            "6c45a65639e89e9ae1f0bec1af042fbe95e4dd33ee6021aed296b66340b6c430": 2,
            "47a01405e62e9a583438026760aa2f98665bb1b018bca1a1c6514b7102452717": 2,
            "bd0c20ddfdb7909915ce3ad307b8853381eb8ac254b7cc821864467ef24aa180": 2,
            "d7013f0cc88f2fcd476de73c0f9185c265f30ba8360f2daee08266200b57eff3": 2,
            "f7eabaf07b3893babd2bbf4dd45d299b58a37b1d2f427ad663e31fca548474b7": 2,
            "8af85b77852fa7dcb7dcc84458809aec20fc7b8b5e3dfcf95577535eeedf2038": 2,
            "6151378c5ec4c044731eef559736ca9a09144c271644f7bfd8970c7d2fa98337": 2,
            "d378d33df53a5fae94860c05a9aabf01307d2ba87c2390a8aa3d3e498fda3cad": 2,
            "61fe819cafee34ef0ec743c08632449235d819798c33ab2b58aeb5db61dd513c": 2,
            "16d37103732ce6572c043e66f2984a2b17ef932aaf23f2eea36d0647f9d834c4": 2,
            "888f2144f5edc5c8c2fc6d279882fa6d88c1569b706f3d2d48644decc8e1d625": 2,
            "73cd3e9923600d1b85b5c82155f677485d0e51eda11b5c4920bfdfcd23e80ca9": 2,
            "c23c7cdf1bdfc47e5c225b6d13299c47a7163cba6c33796f61f89410f0ee3ef3": 2,
            "cfee88ac6aa6f30a5fe918d1bdbaa0e8b005c5df901fae9f8c2eaf9197562051": 2,
            "4ce7351925e110a5808cfdd778192bc2e8a840dcb57ea73add6172c1e663dd0d": 2,
            "e959dde63913605db2d0dc5ed1a4a3c98c1e3f70a36791e6fd593bf831d0bf2d": 2,
            "28489fb10f20a47c036ebfba11584829a6598a3ec1dfae52ce993c6be10bf935": 2,
            "3606173cd73ed348a151ae34794048e217f46e6e4bae83f5c080fdfe27dd0022": 2,
            "b71766e606ac6affb2251fb68f57c9640ba5455266a592f065916b549f3fcbfc": 2,
            "796b2a4d55851a997dae96f0863a527c8820a841e1a90c9712f4cf9fc50a4c29": 2,
            "d65cdbe68edeb9b93bda94d013b1c14fb9fea40e152506d9f1a0032cf6d54db2": 2,
            "0f59b668db9c95502cc3bd02d7eb3d75e5abdae3ae45e15ec927f7cdd7160aa6": 2,
            "7b5cfb2662246bc74f4ad7745f1edfe0656fdbe0116a712fc3af93673945f9a2": 2,
            "d5903b889fe5b457c7303e5b27d51677bcf798a11ed25bb637a4a7ae4eb5cd77": 2,
        },
        "F20": {
            "4ee741213b2e0d8152dbe6ace50c4c176d7c8da6a2f4674778f67c5624a8c63e": 7,
            "0cf091d5b7e0ebfdcb602661efc9b68eef5e52ae51c542219505de7e37c60890": 7,
            "7593157b9e1e456ef6ae8b0d7b46731dddb8396a352a50c5eba6df71db8d37de": 7,
            "8b43f96e5b273cc0da6849dd430443f946c6d850615cf3a1a08d53bbe8e1fdcf": 7,
            "c8f0913f1bf3056b36f23213fdd545e769ce811c03ac03a373824f79fe306cc0": 7,
            "24a6674bc89d8b3cba80cf5a6a74b329701cacc19041909e779988d55e6b47e5": 7,
            "6b146e21f8a507cec0eb4774a19d424a285f0c34a26e38ad1591b42706929d9b": 7,
            "8e4db69594cef603c7bb66c7b362968f5de7087a0179f7c53820e503803b8ffd": 7,
            "25c8b224c274dbe00636c5aca80401f8234ecbef7e3ad641129e863a0fb2deb1": 7,
            "1c6d8f7c4537f2ef496347983c353922d4a5237003551ed9904824aea96fd6f8": 7,
            "e5256704d5fa042264ad22399628972180971767b6cbe156a47add3f16ea60e2": 7,
            "07d23f668e1008b3d33d125e37c17cbd9c9a999d3a05d75ec51ea98b8fea5fd0": 7,
            "c9f7ee31ee28a66f2dee3990b04a2e2feba6e2ba6e13d1db2a28a3f78dd2597b": 7,
            "c4b3c50781880100b9e9e3f35e7612a68a5ad4ecf2791a99a83c8f03928097bb": 7,
            "df558e3560302dac323ccd6334d71e7e959b17aff547358a04a047453352e0b2": 7,
            "136b3f8178791e743eef56f47ebd7b7ad9bfbbfa371a3ffa1c3707de0a2c6e73": 7,
            "9e14250546e7d8a266d9f8661bbe5aa7965d0cfa8517a9a3fe1504c9f09d9a37": 7,
            "96f08d12f6f4f7b6218c21e4525d25c4bdb5a37efdba3d3f71f38f0ebe02b285": 7,
            "e49e2c244459123efc7fa1e81ecb06e86b1ea223a3cd61b8fcaf9202005a2aed": 7,
            "020300d05a69272d4d207d2659f7c33f1be4c7df7316b77220f55437e88ee32b": 7,
            "7c0bc2cb436d637f1e9f1ddeae5fa0f80bf6b3a331219bf9eea2982bc2d0d6f6": 7,
            "74fe1f1bef806bd0df74b39d4931c1db997a2707d1c814723c9cd127a022bb61": 7,
            "f5babe5b7e176e52afa45aa4600647cfe435e1df8bbfad4ccd4e8f80703fd65b": 7,
            "688d5d9737f27d7edafda8dcb96ea0a5396f022860e93f9d065239317f78c254": 7,
            "5cc75cc1a2a4fe4dc1f4319ddde7a7adec586aba931b053cfcbeadc0550763ee": 7,
            "5f010f5812b9cf4ba2d2fa6879a289a95b2fd112630c0f7a10b41c6740e34fa3": 7,
            "29fa4991479c830e1163eeb1e8ac39663ce718f45166a3744587b3eb00ba3aab": 7,
            "e39b0633845c260bba64cdc30968b26c4805f06fed9d81e4153c61f3d7bf6eff": 7,
            "1fec18e0a5eea07eb5af5765756332e68ffb2a4f99254e171a12337ee4cc5312": 7,
            "559889d2d6d9dfefabdcbc391bb7f6cbf4a8092d1caa0e418421296be9fcbb4a": 7,
            "938b9ce811802183145b92f4d45a8b61ef239b2e2cf042e49b408173842a4d92": 7,
            "5da9c325b2cacd183ceb83d342d07f22e835aeda435583716e14d13080533b52": 7,
            "013fd7173f7b748d900e9088a0955c857f1aeb3543fc630d1f2f792d6d8e1a59": 7,
            "814ad344e1579bf81e295a8c46b07000086bda409182747f43b232c5d8b476c1": 7,
            "943996cf5f21f79ab5cb2a9991a4412f2e39c1643a0187b049844159182cb58e": 7,
            "62da3c4c37ce61692120adc4c578386acd058082d081c02870c2e6799e738a9a": 7,
            "7ca996f6f834c70addd870182e3911b783597e7487f7089e20d54750bebb036c": 7,
            "c8d7621d9f532daeb8a37a88627dfd128528705988be06c67dee9d286752a0a9": 7,
            "20260cf99162a6b452f74aa02afff42efce26d590cf5e792c01f9ce72b697092": 7,
            "08b13c4390c6abe494b0af882f4632b1c6ad74618f308e88df4ba7a216cce7ab": 7,
            "efed61bd11e4db288c881d88239a11e24fbcef25b4a8cdcdf2832929599f2ec8": 7,
            "46b78078736e845aabf123f6594ef0ab1f3d48a1e73b6cce0612173afd85d038": 7,
            "0085c6617dc55aaffa65f90931c9055cfd19a2663fcbb3555a11d343b4eeafef": 7,
            "622be5be6e6ac7b8a8506bb0045ef1c0a2ee8aec4958f2550e72e92078a8c805": 7,
            "b2d2ac644ab9bf7f811583c3c63aa5a0ca483ef6f566be2216c1d3dd23f6f656": 7,
            "fb1c3754ef9167a37fe49e27421688632e8ed8871e539289be5385ce483c8520": 7,
            "9d92f8d21be1e1ce1c8d1cf8e025c7feeb6c32143ff4cb0561e484afc7be5e9c": 7,
            "5ead1bb255187d079eaa712e2c15bfeae9ed75fbd32e463c748ac13515d7ab5c": 7,
            "5226ae2d56a23b1350da62b48819c5fd2277cda51e49706919abcfc0dd901985": 7,
            "41b78db864563f25246fe0d601d85470946c01e3c553dd4402da46a54b04d537": 7,
            "8feec1320711ca12a725a17f2edccb55a173bd0ba1da178d21b2e69754555bc8": 7,
            "14e8b99fe109f080f3c3eb861f1f0c03ec5d3bb682c53cd0c41bb9a0b95a2211": 7,
            "d87ef2b962bf83b2a8efb5cb5e56de6f1d5dd9d2093778e57f4cc915d0f79daa": 7,
            "f3743d065f3bdf0df4ad9a902c7f235949f7a0aade296f9744958c5b502f2dbd": 7,
            "bc5990df48e5ada2a7ffbbc1ed92d5290600dd667dfe9d9fc99ac678f4157a85": 7,
            "8ac05c1ef678cc7fca3d98186fd75ea53a2a91f402dd11d53ee406d47aa97465": 7,
            "b529c94a7ad2099c150d743cc31959bd97430d13c2474e46f1579cbfb8011a7b": 7,
            "d01c036ee3ff4072a08a83d3f2fb55305b3fe1b0f60acf525305c337e31fef47": 7,
            "19939b85acb471fb17c076a44c6a0dd173f6c32e5a951986b9e65060458cfd8a": 7,
            "05349d253bf340c2b1a7635fdae210bb53a640f10ca4b960305969801d3bd7ac": 7,
            "d1bc961a69d7cd37ca8763b297c68666f3156fbdcc8961ceced68f3ffaecddfb": 7,
            "5f943991748c5a613ba1d8f8451040a9224e9bd028941d1e63816879b5d1edfe": 7,
            "28b95c60856b1c6a5f88b6e67f7cba9c55b215c10567eeb3b29d2a7e98233175": 7,
        },
        "F46": {
            "63f86e6f8cf6ea2410f90b2d81eaaf47f7691cd4aea01ea3bbfd4f56b5e694c5": 0,
            "7bfe7a5e4cbf014d7eb1b3db687bac09eb8da9c78bef03689ad16364ca93119a": 0,
            "f0bab4c99b2670f6395f757d9d358f52c658d9939ec1024968a06940b15d3289": 0,
            "22b289f6ff3c7046cc90be3bd3bb8effe453dad35b5e1eb2e6d506707ed70f16": 0,
            "9d160f18aa04ddbfe5f332a9fdd9797b08913627a06de7c6b77617c6c316bb0a": 0,
            "9e4a601b7fe5995363999462404028144a853c9f71d5caa5d89cc7a3d5a67b7f": 0,
            "c3935cbfb92962a22254e91f31dde7d86703350b772d1af11dbb2142ebc626f2": 0,
            "d0c65ad8903b5482acb1784b3fe38bbe2c6f7f813fa133e4c0ecdad10a0e4f22": 0,
            "9a5dd89d0805be26d6e52f4a6223266a1128ab987b481ed985c462ebb09b0f1e": 0,
            "04f9941680479efbe74721fbc346c35700d49142036740a31fa19d0f954a233c": 0,
            "3d55634c14b8cf83035539181795676290de80980f9c06519bcc0689abd06070": 0,
            "0a84a82bbf9eb0e996c9146dd77d98b44a0488452287a28046c1c5033b0725ae": 0,
            "dc61060506cbafa838b607bdf2b816a37c7fb675e1fd20cbe59c2aa283cf045b": 0,
            "71bf91e84aa11d374361936959e0896d1e392e70f44d3222943da404fa47a056": 0,
            "7d53b02667577681193f7e31dfe0174808afdc163861a0304dab1dbaa940b9db": 0,
            "3557b4c3c5e83283579cc7e12826798363de63a41fe7a37abbae94adeec24962": 0,
            "f6bd788ba619c77b01ac543f62eab25eff12d6367bf74e89d83d871474720bd5": 0,
            "e01d177554d3ffabeada95d7296c4fe1fd98d83babd88140a79ed50d44c0263b": 0,
            "155f50cc6228c9ee65ae91b9aeaa6c9a5b12a18800a1c2c44910f1df114d9359": 0,
            "b331350eb1d34c6db9083d140cd83819a6dd4bb6df92b26ec97b60f7ded4f17b": 0,
            "1653f229ab703d24c7b2d12c2a485202a17a85d092c1ca8883f4d06e30b8427d": 0,
            "37eb06232c5b346d1f1b35386cfac4666f620a5c91852099345d85082b6afc69": 0,
            "e557305e9830036ce0bb313dbe38b92ed0b05fd7c7db9f95f1d30668f633073b": 0,
            "f93b61f767eafd8a0e5438886171743f84311a2c5a5a964ec0632ad538442e16": 0,
            "9794229c4ba7dcd86a16ba10779910093029f75e36aeb2d3c4b5263053f502f9": 0,
            "b55c1270c3ac0b0c3e757297ccd965a0f03a9ae0720bb66424ceefcc569fad10": 0,
            "82681b78aad237bb0f614ecbcc6164ea2661478fee3551cc415bc33ad3315eb3": 0,
            "64142c5223c82464aec5cc99f0b42ec74322c0f5c8d109504268772149318ca7": 0,
            "e1a69df098eb7d659a8bd0e7e15769777c6efd337749e44c0282dcb0390e7a99": 0,
            "07e01f2598735b9a5b31f143b9e950f2e627ca0f80e32c954fec77e376e45d24": 0,
            "c3865b28c6f2c86d1105d3fb8bd3c86d17479cad61363df04ce450ac0cbfa75b": 0,
            "73ffca05139a5c108a812f0d77692c5e8742f134d74ef3591d3d0f27d618adfa": 0,
            "c242735d3aa59e823ed9db00968828acd6a3a6a536c5b72d2ca4fbe644ddfd06": 0,
            "964c3311475bbd6f623656cc3385591fef2c8c9ca97d90f3cd7a6ac020c623b7": 0,
            "6342bb7c483657aa9ea5c28af08be9c5bb34a79e773b1ad1d4f65ed759a79f67": 0,
            "54c79836aa7697fbcd0b90f23ac50ca7f17dc3d76ca8de4c133a7c1516042cb9": 0,
            "994dc921adc536bb7c22d1ed48e3b33defcf1e9a0a30b1393d508ce5af85a0b9": 0,
            "1ece3263e7f89f584bbe7a6d0d569ccc1afa6efd44570ae4bb87b4059bb7fc68": 0,
            "c23baea19f9bf94d515bc9672893aee73f2a0d27b399247ee4c1f7c53205f046": 0,
            "941c907ce734ca42bd59424e2e8e6950932e2baf00b4624fe19976718ad7943c": 0,
            "5754c99c3dd2f3ba7a50d9e424dc3aa53012ed1b5d1a2bf8a43afc1bd5bf7f79": 0,
            "524ad7469a09527d61ccd6a5f2daa1972e253aa5e98a835fc1abeb58d7461ed0": 0,
            "30765f5822c80b55b4c354578671cf16335f64a3b7d6548a2f7fa84294cf2f33": 0,
            "0fb2395d521fa24ff95ee716c64ff8ee6aa40e95085ef938de50d716e221caac": 0,
            "7674acb44b4f53dc0c91732d67e1d680e51819ac2bcdac7400821a74ef207173": 0,
            "96a1a33494ebf7af4c4cf2146b124fc75f6f489567f8b2281adce25787f5bc59": 0,
            "b8f52a5af2c1e35e21255eecf4cd23471a4950660b0288a8c85a6aa4f5f8a289": 0,
            "56358083ffe71b6213ad7c5001b2bdbf5f836701e9a76a80fa19a87c15da2cd6": 0,
            "48ed4e2e8a666eb96e5b8898285a4a6915253f3d9704cf8512da519202ebb01e": 0,
            "27d26381421a582b657c98ce7eab0502564feea6d58e242de9449fe39d6dcc02": 0,
            "a527f0292b14d944bab5258a428427836024ac8fc01a9289e623b9817d918df0": 0,
            "e37c4246cdbe4b3ad59a9d8ebe92460ed116d0dc54688615b0ea866072072571": 0,
            "ffecf9fa22a8b2991a42274bd803fad88ad43d11c882a2f8a1a36464537f9110": 0,
            "b817180340f3b6200ae45ecd6789af20d2dbc6f66d16ac0ef0979762d8f10384": 0,
            "84dce7450bcd96e0b0608172652e070bf41c511f08554addf32e43df60fc98f7": 0,
            "567197f08dbc77ff3d965d64a9e175a382815ee1476095620eb7a355ba348dc2": 0,
            "1fc2958cef7ad8cccb87f20fcc239a815552f3994fabcb70f951270471b6aeb6": 0,
            "5ead3d4b797994d57c062e09aefba6a6ee88bcbf63d68453d04e1df242ce692f": 0,
            "1380202f048a6f7dbd006041c18ce6eded8d7c1ecd5522f330af6f2023b0a12c": 0,
            "81b5bb21dad42ef84eda6b72b7ff44c693cc8125a6c699f4c86852fdcccb6f64": 0,
            "11917ae9a497ff2cecd420e45bf770b29c2d0e4b0f3700a5c2a3f9e69cdaa8f0": 0,
            "92802201955b3133c9ffb458b99570492a96fe28c7134e0a4eed2724da3223e7": 0,
            "124e4695fc4a4511086b06c8734c9adbd3c7da014f5813540b1e2a81a71edeb3": 0,
        },
    }
    for family, (name, _, _) in records.RECORDS.items():
        document = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        assert (
            frozenset(document["authority"]["permitted_operations"]) == current[family]
        )
        historical = dict(
            getattr(automatic, f"_PRIOR_{family}_QUALIFICATION_MANIFESTS")
        )
        frozen = {
            digest: frozenset(expected["permission_sets"][index])
            for digest, index in expected["families"][family].items()
        }
        assert historical == frozen | {
            digest: frozenset(expected["permission_sets"][index])
            for digest, index in displaced[family].items()
        }
        assert all(options is not current[family] for options in historical.values())
    assert (
        len(
            {
                id(s)
                for f in current
                for _, s in getattr(automatic, f"_PRIOR_{f}_QUALIFICATION_MANIFESTS")
            }
        )
        == 10
    )
    document = json.loads(
        (ROOT / records.REFERENCE / records.RECORDS["F46"][0]).read_bytes()
    )
    assert (
        set(document["authority"]["paired_operations"])
        == audio.F46_PASSING_PAIRED_OPTIONS
    )
    assert len(audio.F46_PASSING_PAIRED_OPTIONS) == 1


def test_all_dispositions_and_public_summary_keep_exact_authority():
    document = json.loads(
        (ROOT / records.REFERENCE / records.RECORDS["F20"][0]).read_bytes()
    )
    assert document["authority"]["dispositions"] == dict(F20_OPTION_DISPOSITIONS)
    assert len(F20_OPTION_DISPOSITIONS) == 19
    assert F20_OPTION_DISPOSITIONS["tags:off"] == "policy_only"
    summary = public_summary()
    assert {entry["candidate_sha256"] for entry in summary} == {
        F20_QUALIFICATION_CANDIDATE_SHA256
    }
    assert {
        f"{e['field']}:{e['option']}" for e in summary if e["automatic_available"]
    } == F20_PASSING_AUTOMATIC_OPTIONS
    assert {e["disposition"] for e in summary} == {
        "passing_automatic",
        "policy_only",
        "passing_human_only",
        "not_qualified",
        "insufficient_coverage",
    }
    with pytest.raises(TypeError):
        F20_OPTION_DISPOSITIONS["tags:off"] = "passing_automatic"


@pytest.mark.parametrize(
    "name",
    [
        "src/mechanical_librarian/domain/rich_metadata_preview.py",
        "src/mechanical_librarian/adapters/providers/hardcover_metadata.py",
        "src/mechanical_librarian/adapters/persistence/field_effect_validation.py",
        "src/mechanical_librarian/application/review_web.py",
        "src/mechanical_librarian/interfaces/web/app.py",
        "src/mechanical_librarian/adapters/persistence/field_effect_activity.py",
    ],
)
def test_semantic_changes_rotate_authority(name):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    baseline = records.semantic_digest(
        provenance, records.source_bindings(ROOT, provenance)
    )
    modified = records.source_bindings(
        ROOT, provenance, {name: (ROOT / name).read_bytes() + b"\n# changed\n"}
    )
    assert records.semantic_digest(provenance, modified) != baseline


@pytest.mark.parametrize(
    "name",
    [
        "frontend/src/routes/PolicyPage.tsx",
        "src/mechanical_librarian/interfaces/web/settings_vocabulary.json",
        "docs/product/rich-metadata-qualification.md",
        "tests/fixtures/qualification-authority-predecessor/provenance.json",
        "src/mechanical_librarian/application/operations.py",
        "tests/unit/test_rich_metadata_qualification_manifest.py",
    ],
)
def test_provenance_changes_do_not_rotate_authority(name):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    bindings = records.source_bindings(ROOT, provenance)
    modified = records.source_bindings(
        ROOT, provenance, {name: (ROOT / name).read_bytes() + b"\n// changed\n"}
    )
    assert modified != bindings
    assert records.semantic_digest(provenance, modified) == records.semantic_digest(
        provenance, bindings
    )


@pytest.mark.parametrize(
    "mutation",
    ["omitted", "unknown", "reclassified", "exclusion", "ast_source", "override"],
)
def test_unreviewed_partition_and_exclusions_refuse(mutation):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    name = "src/mechanical_librarian/domain/proposal_set.py"
    overrides = None
    if mutation == "omitted":
        del provenance["source_classification"][name]
    elif mutation == "unknown":
        provenance["source_classification"]["unknown.py"] = "semantic"
    elif mutation == "reclassified":
        provenance["source_classification"][name] = "provenance"
    elif mutation == "exclusion":
        provenance["binding_exclusions"].append("QUALIFIED_POLICY_OPTIONS")
    elif mutation == "ast_source":
        provenance["ast_sources"].append(name)
    else:
        overrides = {"unknown.py": b""}
    with pytest.raises(ValueError):
        records.source_bindings(ROOT, provenance, overrides)


@pytest.mark.parametrize("name", ["Dockerfile", ".github/workflows/public-gate.yml"])
def test_managed_build_declarations_are_outside_the_inventory(name):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    assert name not in provenance["source_classification"]
    assert name not in provenance["source_bindings"]
    provenance["source_classification"][name] = "provenance"
    with pytest.raises(ValueError, match="requires explicit review"):
        records.source_bindings(ROOT, provenance)


@pytest.mark.parametrize(
    "name", [".github/renovate.json", "tests/unit/test_public_gate_contract.py"]
)
def test_bound_source_edit_drifts(name):
    with pytest.raises(ValueError, match="source binding drift"):
        records.verify(overrides={name: (ROOT / name).read_bytes() + b"\n"})


def test_evidence_and_predecessor_references_are_not_authority():
    for name, _, _ in records.RECORDS.values():
        record = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        changed = copy.deepcopy(record)
        changed["predecessor"]["source_sha"] = "0" * 40
        changed["provenance"] = "different-evidence.json"
        assert records.canonical_digest(
            changed["authority"]
        ) == records.canonical_digest(record["authority"])
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    assert provenance["historical_quality_rescored"] is False
    assert provenance["fresh_holdout_claim"] is False
    assert provenance["production_writes"] == 0
    assert len(provenance["measurement_artifacts"]) == 14
    assert provenance["current_isbn_measurement"]["distinct_cases"] == 19
    assert provenance["current_isbn_measurement"]["unavailable_cases"] == 16
    assert provenance["current_isbn_measurement"]["precision_per_option"] == 1.0
    assert provenance["current_measurement"]["production_writes"] == 0


def test_f38_manifest_separately_qualifies_provider_consensus_contributor_display() -> (
    None
):
    manifest_bytes = F38_MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)

    assert (
        hashlib.sha256(manifest_bytes).hexdigest() == F38_QUALIFICATION_MANIFEST_SHA256
    )
    assert "f38-contributor-display-qualification-manifest-v1" == manifest["schema"]
    assert (
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}" == manifest["policy_identity"]
    )
    assert "decision_only" == manifest["automatic_authority"]
    assert "development_exposed" == manifest["corpus_role"]
    assert False is manifest["holdout_claim"]
    assert {
        "automatic_precision": 1.0,
        "automatic_predictions": 3,
        "automatic_recall": 1.0,
        "positive_opportunities": 3,
        "zero_tolerance_failures": 0,
    } == manifest["metrics"]


@pytest.mark.parametrize(
    "name",
    [
        "new_authority.py",
        "rules.json",
        "rules.txt",
        "interfaces/web/react_assets/rules.json",
        "__pycache__/rules.json",
    ],
)
def test_new_product_source_requires_review(tmp_path, name):
    import shutil

    shutil.copytree(ROOT / "src", tmp_path / "src")
    resource = tmp_path / "src/mechanical_librarian" / name
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text("grant = True\n")
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    with pytest.raises(ValueError, match="product source inventory"):
        records.source_bindings(tmp_path, provenance)


def test_only_named_binding_assignments_break_the_cycle():
    name = "src/mechanical_librarian/domain/automatic_decision.py"
    source = (ROOT / name).read_text()
    bound = source.replace(automatic.F19A_QUALIFICATION_MANIFEST_SHA256, "0" * 64)
    assert records.source_digest(name, source.encode()) == records.source_digest(
        name, bound.encode()
    )
    changed = source.replace('"title_layout:preserve"', '"title_layout:unknown"', 1)
    assert changed != source
    assert records.source_digest(name, source.encode()) != records.source_digest(
        name, changed.encode()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "family",
        "media",
        "predecessor_digest",
        "predecessor_family",
        "origin_digest",
        "origin_family",
        "provenance",
    ],
)
def test_immutable_references_and_family_are_coherent(tmp_path, mutation):
    import shutil

    reference = tmp_path / records.REFERENCE
    reference.mkdir(parents=True)
    for name, _, _ in records.RECORDS.values():
        shutil.copyfile(ROOT / records.REFERENCE / name, reference / name)
    shutil.copyfile(
        ROOT / records.REFERENCE / "qualification-archive-index.json",
        reference / "qualification-archive-index.json",
    )
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    path = reference / records.RECORDS["F19A"][0]
    document = json.loads(path.read_bytes())
    if mutation in {"family", "media"}:
        document["authority"][mutation] = "F46" if mutation == "family" else "audiobook"
    elif mutation == "predecessor_digest":
        document["predecessor"]["sha256"] = "0" * 64
    elif mutation == "predecessor_family":
        document["predecessor"] = json.loads(
            (reference / records.RECORDS["F46"][0]).read_bytes()
        )["predecessor"]
    elif mutation == "origin_digest":
        provenance["historical_measurement_origins"]["F19A"]["sha256"] = "0" * 64
    elif mutation == "origin_family":
        provenance["historical_measurement_origins"]["F19A"] = provenance[
            "historical_measurement_origins"
        ]["F46"]
    else:
        document["provenance"] = "unbound.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        records.verify_references(tmp_path, provenance)


def test_retained_measurement_context_matches_immutable_base():
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    # Independently captured from the base F46-v20 / F20-v51 records; no archive lookup.
    expected = {
        "current_measurement": "5bc75d52d821e9f91607d0a567382162677bee954a8b4185891874260883a760",
        "measurement_artifacts": "131f350ce18f5d4dc1df46f6364278af148027a95c0db63c5b6b8cd51f698151",
        "limitations": "a5ff8bf309e9f8c5881c3804a4018336f23093a90a707942eb1e1d4186d04669",
        "current_ebook_measurement": "68257847af41acebfa3fbc20bbe61bdc4ae9e2a8df4a6f2aeea1cd4214258efc",
    }
    for key, digest in expected.items():
        assert records.canonical_digest(provenance[key]) == digest


@pytest.mark.parametrize("family", list(records.RECORDS))
@pytest.mark.parametrize("scope", ["record", "authority"])
def test_closed_record_keysets_refuse_before_verify_or_refresh(tmp_path, family, scope):
    import shutil

    reference = tmp_path / records.REFERENCE
    reference.mkdir(parents=True)
    for path in [
        ROOT / records.PROVENANCE,
        ROOT / records.REFERENCE / "qualification-archive-index.json",
        *[ROOT / records.REFERENCE / name for name, _, _ in records.RECORDS.values()],
    ]:
        shutil.copyfile(path, reference / path.name)
    path = reference / records.RECORDS[family][0]
    original = json.loads(path.read_bytes())
    target = original if scope == "record" else original["authority"]
    for field in [*target, "incidental_evidence"]:
        changed = copy.deepcopy(original)
        destination = changed if scope == "record" else changed["authority"]
        if field in target:
            del destination[field]
        else:
            destination[field] = "not authority"
        path.write_text(json.dumps(changed))
        before = {p.name: p.read_bytes() for p in reference.iterdir()}
        for operation in (records.verify, records.refresh):
            with pytest.raises(ValueError, match=f"qualification {scope} keys"):
                operation(tmp_path)
            assert {p.name: p.read_bytes() for p in reference.iterdir()} == before


@pytest.mark.parametrize(
    "declaration",
    [
        "",
        'BOUND = "a" * 64',
        'BOUND = "' + "0" * 64 + '"\nBOUND = "' + "1" * 64 + '"',
        "BOUND: str",
    ],
)
def test_binding_assignment_requires_one_top_level_literal(declaration):
    with pytest.raises(ValueError, match="runtime binding assignment"):
        records.binding_value('# BOUND = "' + "2" * 64 + '"\n' + declaration, "BOUND")


@pytest.mark.parametrize("family", list(records.RECORDS))
@pytest.mark.parametrize("decoy", ["comment", "string"])
def test_refresh_changes_actual_imported_binding_not_decoy(tmp_path, family, decoy):
    import runpy
    import shutil

    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    for name in provenance["source_classification"]:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    name, module, constant = records.RECORDS[family]
    module_path = tmp_path / "src/mechanical_librarian/domain" / module
    source = module_path.read_text()
    old = records.binding_value(source, constant).value
    assert type(old) is str
    example = f'{constant} = "{old}"'
    source = (
        "# " + example + "\n" + source
        if decoy == "comment"
        else source.replace('"""', '"""' + example + "\n", 1)
    )
    module_path.write_text(source)
    record_path = tmp_path / records.REFERENCE / name
    record = json.loads(record_path.read_bytes())
    record["authority"]["semantic_revision"] += 1
    record_path.write_text(json.dumps(record))
    records.refresh(tmp_path)
    records.verify(tmp_path)
    expected = records.canonical_digest(
        json.loads(record_path.read_bytes())["authority"]
    )
    assert expected != old
    assert example in module_path.read_text()
    assert runpy.run_path(str(module_path))[constant] == expected
    # A matching comment must not conceal a stale actual value from verification.
    source = module_path.read_text().replace(f'"{expected}"', '"' + "0" * 64 + '"')
    module_path.write_text(f'# {constant} = "{expected}"\n' + source)
    with pytest.raises(ValueError, match="runtime authority binding drift"):
        records.verify(tmp_path)


@pytest.mark.parametrize("family", ["F19A", "F20", "F46"])
def test_activity_predecessor_producer_decisions_decode_without_current_authority(
    family,
):
    fixture = json.loads(
        (
            ROOT / "tests/fixtures/activity-qualification-predecessor-decisions.json"
        ).read_bytes()
    )
    assert fixture["source_sha"] == "43359510da9cc04aed874714728d1581801021e9"
    assert len(fixture["documents"][family]) == 1
    for document in fixture["documents"][family]:
        decision = automatic.AutomaticDecision.from_json(document)
        assert decision.to_json() == document
        assert not automatic.qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
