"""Adversarial identity-gate cases (Codex red-team, 4 Oct 2026). One accepted wrong site would
block qualification, so every case here must stay rejected; the positive controls must pass."""
import json
import unittest

from fjordlens import gate
from fjordlens.html import parse_html

ORG, SISTER, OTHER = "923609016", "990888213", "984862296"


def page(html, url="https://example.no/"):
    return parse_html(html, url)


def ctx(name, **extra):
    return gate.Context({"organisasjonsnummer": ORG, "navn": name, **extra})


def jsonld(value):
    return '<script type="application/ld+json">' + json.dumps(value) + "</script>"


def decide(name, origin, pages, **extra):
    return gate.assess(ctx(name, **extra), {"url": pages[0]["url"], "origin": origin}, pages)


class GateAdversarialTests(unittest.TestCase):
    def test_agency_credit_in_client_footer_is_not_ownership(self):
        html = ("<title>Restaurant Fjord | Meny</title><p>Velkommen til Restaurant Fjord i Bergen. Vi serverer sjømat.</p>"
                f"<footer>Restaurant Fjord - Website by Pixel Agency AS, Org nr {ORG}</footer>")
        result = decide("PIXEL AGENCY AS", "registry_website", [page(html, "https://client.no/")])
        self.assertFalse(result["publishable"], result)

    def test_article_subject_jsonld_is_not_ownership(self):
        html = ("<title>Avisen - nyheter</title><p>Nyheter fra hele landet om energi og olje.</p>"
                + jsonld({"@type": "NewsArticle", "headline": "Equinor øker", "about": {"@type": "Organization", "legalName": "EQUINOR ASA",
                                                                                         "taxID": ORG, "url": "https://equinor.com/"}}))
        result = decide("EQUINOR ASA", "dns_guess", [page(html, "https://avisen.no/")])
        self.assertFalse(result["publishable"], result)

    def test_landlord_titled_with_longer_name_is_rejected_despite_customer_mention(self):
        html = ("<title>Bergen Bil Eiendom AS</title><p>Vi leier ut lokaler til Bergen Bil AS og andre leietakere.</p>"
                "<p>Telefon 55 55 55 55</p>")
        result = decide("BERGEN BIL AS", "dns_guess", [page(html, "https://bergenbil.no/")], telefon="55555555")
        self.assertFalse(result["publishable"], result)

    def test_group_homepage_listing_several_org_numbers_is_rejected(self):
        html = (f"<title>Bergen Bil Gruppen</title><p>Bergen Bil AS (org.nr {ORG}) og Bergen Bil Service AS "
                f"(org.nr {SISTER}) er en del av gruppen.</p>")
        result = decide("BERGEN BIL AS", "registry_website", [page(html, "https://bergenbilgruppen.no/")])
        self.assertFalse(result["publishable"], result)

    def test_group_word_in_owner_string_is_not_our_name(self):
        html = "<title>Bergen Bil Gruppen</title><p>Biler, service og deler i hele Vestland.</p>" * 2
        result = decide("BERGEN BIL AS", "registry_website", [page(html, "https://bergenbilgruppen.no/")])
        self.assertFalse(result["publishable"], result)

    def test_parent_page_listing_subsidiary_number_is_not_subsidiary_site(self):
        home = page("<title>NORDIC GROUP</title><p>The Nordic Group owns businesses across many sectors.</p>"
                    '<a href="/selskaper/nord-as">Nord AS</a>', "https://nordic.example/")
        sub = page(f"<title>NORD AS</title><p>Nord AS Org nr {ORG}</p>", "https://nordic.example/selskaper/nord-as")
        result = decide("NORD AS", "registry_website", [home, sub])
        self.assertFalse(result["publishable"], result)

    def test_namesake_guess_without_contact_or_number_is_ambiguous(self):
        html = "<title>Gudrun Bråkkan - keramikk</title><p>Gudrun lager keramikk i Oslo og selger på markeder.</p>"
        result = decide("GUDRUN AS", "dns_guess", [page(html, "https://gudrun.no/")])
        self.assertFalse(result["publishable"], result)

    def test_parked_domain_is_rejected(self):
        result = decide("SPOR ARKITEKTER AS", "registry_website", [page("<title>spor.no</title><p>This domain is for sale. Buy this domain today!</p>")])
        self.assertFalse(result["publishable"])
        self.assertTrue(result["hard_reject"])

    def test_foreign_com_guess_is_rejected(self):
        html = "<html lang='en'><title>SPC Solutions Inc</title><p>We sell industrial parts across the United States and Canada.</p></html>"
        result = decide("SPC SOLUTIONS AS", "dns_guess", [page(html, "https://spcsolutions.com/")], telefon="22334455")
        self.assertFalse(result["publishable"])

    def test_positive_org_number_in_own_footer(self):
        html = f"<title>Videolabben</title><p>Video for bedrifter.</p><footer>Videolabben AS - Mor Åses Vei 4, 1535 Moss - Org.nr {ORG}</footer>"
        result = decide("VIDEOLABBEN AS", "dns_guess", [page(html, "https://videolabben.no/")])
        self.assertTrue(result["publishable"], result)
        self.assertIn(ORG, result["proof_span"].replace(" ", ""))

    def test_positive_declared_site_with_exact_name(self):
        html = ('<title>Bauge AS - Hjem</title><a href="/kontakt">Kontakt</a><a href="/om-oss">Om oss</a>'
                "<p>Bauge AS leverer tømrertjenester i Nordhordland siden 1990.</p>")
        result = decide("BAUGE AS", "registry_website", [page(html, "https://www.bauge-as.no/")])
        self.assertTrue(result["publishable"], result)

    def test_domain_placeholder_title_is_not_name_evidence(self):
        html = '<title>sognestal.no</title><a href="/">sognestal.no</a><p>Velkommen. Denne siden er under oppbygging.</p>'
        result = decide("SØGNE STÅL AS", "registry_website", [page(html, "http://sognestal.no/")])
        self.assertFalse(result["publishable"], result)

    def test_guess_with_name_and_registered_ceo_only_is_not_proof(self):
        html = ('<title>Persona Norge</title><a href="/om">Om oss</a>'
                "<p>Persona Norge leverer rådgivning. Daglig leder Kari Nordmann svarer gjerne på spørsmål.</p>")
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "PERSONA NORGE AS"}, people=["Kari Elisabeth Nordmann"])
        result = gate.assess(context, {"url": "https://personanorge.no/", "origin": "dns_guess"}, [page(html, "https://personanorge.no/")])
        self.assertFalse(result["publishable"], result)

    def test_guess_with_name_and_different_person_stays_ambiguous(self):
        html = ('<title>Persona Norge</title><a href="/om">Om oss</a>'
                "<p>Persona Norge leverer rådgivning. Daglig leder Ola Hansen svarer gjerne på spørsmål.</p>")
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "PERSONA NORGE AS"}, people=["Kari Nordmann"])
        result = gate.assess(context, {"url": "https://personanorge.no/", "origin": "dns_guess"}, [page(html, "https://personanorge.no/")])
        self.assertFalse(result["publishable"], result)

    def test_brand_guess_needs_our_number_in_owner_position(self):
        html = '<title>Restaurant Fjord</title><a href="/meny">Meny</a><p>Sjømat ved bryggen i Bergen, åpent hver dag.</p>'
        without = decide("HAVBRIS HOLDING AS", "dns_guess_brand", [page(html, "https://restaurantfjord.no/")])
        self.assertFalse(without["publishable"], without)
        with_number = decide("HAVBRIS HOLDING AS", "dns_guess_brand",
                             [page(html + f"<footer>Restaurant Fjord drives av Havbris Holding AS, org.nr {ORG}</footer>", "https://restaurantfjord.no/")])
        self.assertTrue(with_number["publishable"], with_number)
        self.assertTrue(with_number["rule"].startswith("A"))

    def test_guess_redirected_to_domain_marketplace_is_rejected(self):
        # Live case 4 Oct: danielolsen.com redirected to a HugeDomains sale page; DANIEL OLSEN AS's
        # registered person shares the company's name, so "person on site" proved nothing.
        html = ("<title>DanielOlsen.com is for sale | HugeDomains</title><a href='/buy'>Buy now</a>"
                + "<p>Daniel Olsen premium domain. Secure checkout, fast transfer, payment plans available.</p>" * 60)
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "DANIEL OLSEN AS"}, people=["Daniel Olsen"])
        result = gate.assess(context, {"url": "https://danielolsen.com/", "origin": "dns_guess"},
                             [page(html, "https://www.hugedomains.com/domain_profile.cfm?d=danielolsen.com")])
        self.assertFalse(result["publishable"], result)

    def test_person_named_company_cannot_use_its_own_name_as_person_evidence(self):
        html = '<title>Daniel Olsen</title><a href="/om">Om</a><p>Daniel Olsen er fotograf i Bodø og tar oppdrag for bedrifter.</p>'
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "DANIEL OLSEN AS"}, people=["Daniel Olsen"])
        result = gate.assess(context, {"url": "https://danielolsen.no/", "origin": "dns_guess"}, [page(html, "https://danielolsen.no/")])
        self.assertFalse(result["publishable"], result)

    def test_guess_redirecting_to_other_domain_needs_our_number(self):
        html = '<title>Bergen Rør AS</title><a href="/kontakt">Kontakt</a><p>Bergen Rør AS leverer rørleggertjenester. Ring 55 11 22 33.</p>'
        context = ctx("BERGEN RØR AS", telefon="55112233")
        result = gate.assess(context, {"url": "https://bergenror.no/", "origin": "dns_guess"}, [page(html, "https://rorgruppen.no/bergen")])
        self.assertFalse(result["publishable"], result)
        self.assertTrue(result["signals"].get("redirected_to_other_domain"))

    def test_guess_with_name_and_registry_phone_only_is_not_proof(self):
        html = ('<title>Haugetun Catering AS</title><a href="/meny">Meny</a>'
                "<p>Ring oss på 55 12 34 56 for bestilling av mat til selskap.</p>")
        result = decide("HAUGETUN CATERING AS", "dns_guess", [page(html, "https://haugetuncatering.no/")], telefon="55123456")
        self.assertFalse(result["publishable"], result)

    def test_positive_guess_with_name_and_postcode_with_town(self):
        html = ('<title>Haugetun Catering AS</title><a href="/meny">Meny</a>'
                "<p>Mat til selskap. Haugetunvegen 3, 5550 Sveio.</p>")
        result = decide("HAUGETUN CATERING AS", "dns_guess", [page(html, "https://haugetuncatering.no/")],
                        forretningsadresse={"adresse": ["Haugetunvegen 3"], "postnummer": "5550", "poststed": "SVEIO"})
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("D:"))
        self.assertTrue(result["signals"]["registry_postcode_town_on_site"])

    def test_fjords_travel_guide_is_not_the_fjords_da(self):
        # Builderr's second evaluation report: fjords.com was published for THE FJORDS DA. The page named the
        # village (also the registered "street"), but neither the organisation number, the legal name nor the
        # registered address.
        html = ("<title>Flåm and Aurland travel guide | fjords.com</title>"
                "<p>Discover the fjords: Flåm Railway, Aurland lookout and cruises from Flåm. Book your fjord trip today.</p>")
        result = decide("THE FJORDS DA", "dns_guess", [page(html, "https://www.fjords.com/")],
                        forretningsadresse={"adresse": ["Flåm"], "postnummer": "5742", "poststed": "FLÅM"})
        self.assertFalse(result["publishable"], result)


class DeclaredSiteRuleTests(unittest.TestCase):
    """Rules A2 and C: sites the company itself declared, proven without the name in the title."""
    BODY = "<p>Vi utfører alt innen rehabilitering, tilbygg og nybygg i hele Rogaland.</p>"

    def test_c_registry_site_with_exact_name_and_registry_phone_is_accepted(self):
        html = "<title>Home</title><p>Velkommen til RHK BYGG AS.</p>" + self.BODY + "<p>Ring oss på 909 12 345</p>"
        result = decide("RHK BYGG AS", "registry_website", [page(html, "https://www.rhk-bygg.no/")], telefon="90912345")
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("C:"))
        self.assertIn("RHK BYGG AS", result["proof_span"])

    def test_c_needs_a_registry_contact_or_person(self):
        html = "<title>Home</title><p>Velkommen til RHK BYGG AS.</p>" + self.BODY
        result = decide("RHK BYGG AS", "registry_website", [page(html, "https://www.rhk-bygg.no/")], telefon="90912345")
        self.assertFalse(result["publishable"], result)

    def test_c_does_not_apply_to_guessed_or_email_domains(self):
        html = "<title>Home</title><p>Velkommen til RHK BYGG AS.</p>" + self.BODY + "<p>Ring oss på 909 12 345</p>"
        for origin in ("dns_guess", "registry_email_domain"):
            with self.subTest(origin=origin):
                result = decide("RHK BYGG AS", origin, [page(html, "https://www.rhk-bygg.no/")], telefon="90912345")
                self.assertFalse(result["publishable"], result)

    def test_c_rejects_parent_site_that_also_names_a_longer_sister(self):
        html = ("<title>Stoltz</title><p>Stoltz Entreprenør AS og Stoltz Entreprenør Eiendom AS er en del av konsernet.</p>"
                + self.BODY + "<p>Telefon 55 12 34 56</p>")
        result = decide("STOLTZ ENTREPRENØR AS", "registry_website", [page(html, "https://www.stoltz.no/")], telefon="55123456")
        self.assertFalse(result["publishable"], result)

    def test_c_rejects_group_owner_string(self):
        html = ("<title>Holmen Gruppen</title><p>Holmen Bygg AS er et av selskapene våre.</p>" + self.BODY + "<p>Telefon 55 12 34 56</p>")
        result = decide("HOLMEN BYGG AS", "registry_website", [page(html, "https://holmengruppen.no/")], telefon="55123456")
        self.assertFalse(result["publishable"], result)
        # Same text on a neutral domain and title: the words around our name still frame a group.
        html = ("<title>Velkommen</title><p>Holmen Bygg AS er et av selskapene våre.</p>" + self.BODY + "<p>Telefon 55 12 34 56</p>")
        result = decide("HOLMEN BYGG AS", "registry_website", [page(html, "https://holmen.no/")], telefon="55123456")
        self.assertFalse(result["publishable"], result)

    def test_a2_declared_site_with_only_our_number_on_a_subpage(self):
        home = page("<title>Ajour</title><p>Regnskap og lønn for små og mellomstore bedrifter i hele landet. Vi leverer "
                    "systemer, opplæring og support til kunder i alle bransjer.</p>", "https://ajour.no/")
        about = page(f"<title>Om oss</title><p>Ajour Data AS ble etablert i 1997.</p><p>Telefon +47 908 30 512 Org.nr: {ORG}</p>",
                     "https://ajour.no/om-oss/")
        result = decide("AJOUR DATA AS", "registry_email_domain", [home, about], telefon="90830512")
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("A2:"))
        self.assertIn(ORG, result["proof_span"])
        self.assertEqual(result["proof_url"], "https://ajour.no/om-oss/")

    def test_a2_rejects_when_another_number_is_on_the_site(self):
        home = page("<title>Ajour</title><p>Regnskap og lønn for små og mellomstore bedrifter i hele landet.</p>", "https://ajour.no/")
        about = page(f"<title>Om oss</title><p>Ajour Data AS ble etablert i 1997. Ajour Gruppen AS org.nr {SISTER}.</p>"
                     f"<p>Telefon +47 908 30 512 Org.nr: {ORG}</p>", "https://ajour.no/om-oss/")
        result = decide("AJOUR DATA AS", "registry_email_domain", [home, about], telefon="90830512")
        self.assertFalse(result["publishable"], result)


class GuessedExactNameTests(unittest.TestCase):
    """Rule D3: our exact registered name with our own legal form in the site's owner strings."""
    BODY = "<p>Vi leverer trehus, hytter og tilbygg over hele Vestlandet. Kontakt oss for et uforpliktende tilbud.</p>"

    def test_exact_name_with_suffix_alone_is_not_proof(self):
        html = "<title>Hjem</title>" + self.BODY + "<footer>Copyright © 2006-2026 Arona Trehus AS. All rights reserved</footer>"
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
        self.assertFalse(result["publishable"], result)

    def test_exact_name_with_registered_postcode_and_town_is_proof(self):
        html = ("<title>Hjem</title>" + self.BODY +
                "<footer>Copyright © 2006-2026 Arona Trehus AS. Industrivegen 2, 5550 Sveio</footer>")
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")],
                        forretningsadresse={"adresse": ["Industrivegen 2"], "postnummer": "5550", "poststed": "SVEIO"})
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("D:"))

    def test_d3_rejects_name_preceded_by_another_capitalised_word(self):
        html = "<title>Nye Arona Trehus AS</title>" + self.BODY
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
        self.assertFalse(result["publishable"], result)

    def test_d3_rejects_other_legal_form(self):
        html = "<title>Arona Trehus SA</title>" + self.BODY
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
        self.assertFalse(result["publishable"], result)

    def test_d3_rejects_name_without_legal_form(self):
        html = "<title>Arona Trehus - hytter og hus</title>" + self.BODY
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
        self.assertFalse(result["publishable"], result)

    def test_d3_rejects_chain_subdomain(self):
        html = "<title>Byggmann Stjørdal Meråker AS - gratis huskatalog</title>" + self.BODY
        result = decide("BYGGMANN STJØRDAL MERÅKER AS", "dns_guess", [page(html, "https://stjordal.byggmann.no/")])
        self.assertFalse(result["publishable"], result)

    def test_d3_rejects_longer_sister_name_on_site(self):
        html = "<title>Arona Trehus AS</title>" + self.BODY + "<p>Arona Trehus Bygg AS står for montering.</p>"
        result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
        self.assertFalse(result["publishable"], result)


class WorkplaceDeclarationTests(unittest.TestCase):
    """Websites and e-mail domains registered on a subunit are company declarations; subunit phones corroborate."""
    BODY = "<p>Bilverksted, dekkhotell og EU-kontroll i Lye. Vi har lang erfaring med alle bilmerker.</p>"

    def test_workplace_website_with_name_in_title_is_accepted_by_rule_b(self):
        html = "<title>Lye Bil AS - bilverksted</title>" + self.BODY
        result = decide("LYE BIL AS", "registry_workplace_website", [page(html, "https://www.lyebil.no/")])
        self.assertTrue(result["publishable"], result)
        self.assertTrue(result["rule"].startswith("B:"))

    def test_workplace_website_of_a_partner_is_rejected(self):
        html = "<title>Visma - programvare</title><p>Regnskap, lønn og ERP for norske bedrifter av alle størrelser.</p>"
        result = decide("AZETS INSIGHT AS", "registry_workplace_website", [page(html, "https://www.visma.no/")])
        self.assertFalse(result["publishable"], result)

    def test_workplace_phone_alone_does_not_prove_a_guessed_domain(self):
        html = "<title>Lye Bil</title>" + self.BODY + "<p>Ring 51 12 34 56</p>"
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "LYE BIL AS"}, [{"telefon": "51 12 34 56"}])
        result = gate.assess(context, {"url": "https://www.lyebil.no/", "origin": "dns_guess"}, [page(html, "https://www.lyebil.no/")])
        self.assertFalse(result["publishable"], result)
        self.assertTrue(result["signals"]["registry_phone_on_site"])

    def test_workplace_postcode_with_town_proves_a_guessed_domain(self):
        html = "<title>Lye Bil</title>" + self.BODY + "<p>Lyevegen 10, 4365 Nærbø</p>"
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "LYE BIL AS"},
                               [{"adresse": ["Lyevegen 10"], "postnummer": "4365", "poststed": "NÆRBØ"}])
        result = gate.assess(context, {"url": "https://www.lyebil.no/", "origin": "dns_guess"}, [page(html, "https://www.lyebil.no/")])
        self.assertTrue(result["publishable"], result)

    def test_workplace_email_on_its_own_domain_is_circular(self):
        html = "<title>Hjem</title>" + self.BODY + "<p>post@lyebil.no</p>"
        context = gate.Context({"organisasjonsnummer": ORG, "navn": "LYE BIL AS"}, [{"epostadresse": "post@lyebil.no"}])
        result = gate.assess(context, {"url": "https://lyebil.no/", "origin": "registry_workplace_email_domain"}, [page(html, "https://lyebil.no/")])
        self.assertFalse(result["signals"]["registry_email_on_site"], result)


class RedTeamBoundaryTests(unittest.TestCase):
    """Names joined by connectors are different registered names; multi-company pages abstain."""
    BODY = "<p>Vi leverer trehus, hytter og tilbygg over hele Vestlandet. Kontakt oss for et uforpliktende tilbud.</p>"

    def test_d3_rejects_names_joined_by_connectors(self):
        for title in ("Hammer & Arona Trehus AS", "Hansen og Arona Trehus AS", "Nord-Arona Trehus AS"):
            with self.subTest(title=title):
                html = f"<title>{title}</title>" + self.BODY
                result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")])
                self.assertFalse(result["publishable"], result)

    def test_separated_and_lead_in_forms_with_registered_postcode_are_accepted(self):
        for title in ("Hjem | Arona Trehus AS", "Forside - Arona Trehus AS", "Velkommen til Arona Trehus AS", "Om oss: Arona Trehus AS"):
            with self.subTest(title=title):
                html = f"<title>{title}</title>" + self.BODY + "<p>5550 Sveio</p>"
                result = decide("ARONA TREHUS AS", "dns_guess", [page(html, "https://www.aronatrehus.no/")],
                                forretningsadresse={"adresse": [], "postnummer": "5550", "poststed": "SVEIO"})
                self.assertTrue(result["publishable"], result)

    def test_c_abstains_on_a_manager_page_listing_several_companies(self):
        html = ("<title>Forvaltning</title><p>Vi forvalter Ski Logistikkpark 5 AS, Ski Logistikkpark 6 AS og Ski Logistikkpark 7 AS.</p>"
                "<p>Kjøpmannsvegen 12, 1400 Ski. Telefon 64 12 34 56</p>" + self.BODY)
        result = decide("SKI LOGISTIKKPARK 5 AS", "registry_website", [page(html, "https://forvalter.no/")], telefon="64123456")
        self.assertFalse(result["publishable"], result)


if __name__ == "__main__":
    unittest.main()
