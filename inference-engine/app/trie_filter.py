from typing import Dict, Iterable
from .schemas import StageResult


class TrieNode:
    def __init__(self):
        self.children: Dict[str, "TrieNode"] = {}
        self.is_end_of_word: bool = False


class ProfanityFilterTrie:
    def __init__(self, bad_words: Iterable[str]):
        self._root = TrieNode()
        for word in bad_words:
            self.insert(word)

    def insert(self, word: str):
        current = self._root
        for ch in word:
            if ch not in current.children:
                current.children[ch] = TrieNode()
            current = current.children[ch]
        current.is_end_of_word = True

    def _is_noise(self, char: str) -> bool:
        if not char.isalpha():
            return True
        if "\u3131" <= char <= "\u318E":
            return True
        if char.isascii() and char.isalpha():
            return True
        return False

    def convert_qwerty_to_hangul(self, text: str) -> str:
        text = text.lower()
        en_ko = {
            "q": "ㅂ", "w": "ㅈ", "e": "ㄷ", "r": "ㄱ", "t": "ㅅ", "y": "ㅛ",
            "u": "ㅕ", "i": "ㅑ", "o": "ㅐ", "p": "ㅔ", "a": "ㅁ", "s": "ㄴ",
            "d": "ㅇ", "f": "ㄹ", "g": "ㅎ", "h": "ㅗ", "j": "ㅓ", "k": "ㅏ",
            "l": "ㅣ", "z": "ㅋ", "x": "ㅌ", "c": "ㅊ", "v": "ㅍ", "b": "ㅠ",
            "n": "ㅜ", "m": "ㅡ",
        }
        cho = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
        jung = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
        jong = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
        compound_jung = {
            ("ㅗ", "ㅏ"): "ㅘ",
            ("ㅗ", "ㅐ"): "ㅙ",
            ("ㅗ", "ㅣ"): "ㅚ",
            ("ㅜ", "ㅓ"): "ㅝ",
            ("ㅜ", "ㅔ"): "ㅞ",
            ("ㅜ", "ㅣ"): "ㅟ",
            ("ㅡ", "ㅣ"): "ㅢ",
        }
        compound_jong = {
            ("ㄱ", "ㅅ"): "ㄳ",
            ("ㄴ", "ㅈ"): "ㄵ",
            ("ㄴ", "ㅎ"): "ㄶ",
            ("ㄹ", "ㄱ"): "ㄺ",
            ("ㄹ", "ㅁ"): "ㄻ",
            ("ㄹ", "ㅂ"): "ㄼ",
            ("ㄹ", "ㅅ"): "ㄽ",
            ("ㄹ", "ㅌ"): "ㄾ",
            ("ㄹ", "ㅍ"): "ㄿ",
            ("ㄹ", "ㅎ"): "ㅀ",
            ("ㅂ", "ㅅ"): "ㅄ",
        }

        def is_cho(char: str) -> bool:
            return char in cho

        def is_jung(char: str) -> bool:
            return char in jung

        def is_jong(char: str) -> bool:
            return char in jong and char != " "

        result = ""
        index = 0
        jamos = [en_ko.get(char, char) for char in text]

        while index < len(jamos):
            current_cho = jamos[index]
            if not is_cho(current_cho):
                result += current_cho
                index += 1
                continue

            if index + 1 < len(jamos) and is_jung(jamos[index + 1]):
                current_jung = jamos[index + 1]
                index += 2

                if index < len(jamos) and is_jung(jamos[index]) and (current_jung, jamos[index]) in compound_jung:
                    current_jung = compound_jung[(current_jung, jamos[index])]
                    index += 1

                current_jong = " "
                if index < len(jamos) and is_jong(jamos[index]):
                    if index + 1 < len(jamos) and is_jung(jamos[index + 1]):
                        pass
                    else:
                        current_jong = jamos[index]
                        index += 1
                        if index < len(jamos) and is_jong(jamos[index]):
                            if (current_jong, jamos[index]) in compound_jong:
                                if index + 1 < len(jamos) and is_jung(jamos[index + 1]):
                                    pass
                                else:
                                    current_jong = compound_jong[(current_jong, jamos[index])]
                                    index += 1

                cho_idx = cho.index(current_cho)
                jung_idx = jung.index(current_jung)
                jong_idx = jong.index(current_jong)
                result += chr(0xAC00 + (cho_idx * 21 * 28) + (jung_idx * 28) + jong_idx)
            else:
                result += current_cho
                index += 1

        return result

    def convert_roman_to_hangul(self, text: str) -> str:
        text = text.lower()
        cho_map = {
            "kk": "ㄲ", "tt": "ㄸ", "pp": "ㅃ", "ss": "ㅆ", "jj": "ㅉ", "ch": "ㅊ", "sh": "ㅅ",
            "g": "ㄱ", "n": "ㄴ", "d": "ㄷ", "r": "ㄹ", "l": "ㄹ", "m": "ㅁ", "b": "ㅂ", "s": "ㅅ",
            "j": "ㅈ", "c": "ㅊ", "k": "ㅋ", "t": "ㅌ", "p": "ㅍ", "h": "ㅎ",
        }
        jung_map = {
            "yae": "ㅒ", "ya": "ㅑ", "ae": "ㅐ", "a": "ㅏ", "eo": "ㅓ", "e": "ㅔ", "yeo": "ㅕ", "ye": "ㅖ",
            "wae": "ㅙ", "wa": "ㅘ", "oe": "ㅚ", "o": "ㅗ", "yo": "ㅛ", "wo": "ㅝ", "we": "ㅞ", "wi": "ㅟ",
            "yu": "ㅠ", "u": "ㅜ", "oo": "ㅜ", "eu": "ㅡ", "ui": "ㅢ", "i": "ㅣ",
        }
        jong_map = {
            "ng": "ㅇ", "kk": "ㄲ", "k": "ㄱ", "g": "ㄱ", "n": "ㄴ", "t": "ㅅ", "d": "ㄷ", "l": "ㄹ",
            "r": "ㄹ", "m": "ㅁ", "p": "ㅂ", "b": "ㅂ",
        }
        cho = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
        jung = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
        jong = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"

        result = ""
        index = 0
        while index < len(text):
            cho_val = None
            cho_len = 0
            for key in cho_map:
                if text.startswith(key, index):
                    cho_val = cho_map[key]
                    cho_len = len(key)
                    break
            if not cho_val:
                cho_val = "ㅇ"
                cho_len = 0

            jung_val = None
            jung_len = 0
            for key in jung_map:
                if text.startswith(key, index + cho_len):
                    jung_val = jung_map[key]
                    jung_len = len(key)
                    break

            if not jung_val:
                if cho_len > 0:
                    result += text[index:index + cho_len]
                    index += cho_len
                else:
                    result += text[index]
                    index += 1
                continue

            jong_val = " "
            jong_len = 0
            next_index = index + cho_len + jung_len
            for key in jong_map:
                if text.startswith(key, next_index):
                    after_jong = next_index + len(key)
                    next_is_jung = any(text.startswith(jung_key, after_jong) for jung_key in jung_map)
                    if not next_is_jung:
                        jong_val = jong_map[key]
                        jong_len = len(key)
                    break

            cho_idx = cho.index(cho_val)
            jung_idx = jung.index(jung_val)
            jong_idx = jong.index(jong_val)
            result += chr(0xAC00 + (cho_idx * 21 * 28) + (jung_idx * 28) + jong_idx)
            index += cho_len + jung_len + jong_len

        return result

    # 기존 ProfanityFilterTrie 클래스 내부의 normalize_text 함수를 아래 버전으로 덮어쓰세요.
    def normalize_text(self, text: str) -> str:
        import re
        text = text.lower()
        
        text = re.sub(r'시+이*발+', '시발', text)
        text = re.sub(r'씨+이*발+', '씨발', text)
        text = re.sub(r'존+나+', '존나', text)
        text = re.sub(r'지+랄+', '지랄', text)
        text = re.sub(r'미+친+', '미친', text)
        text = re.sub(r'병+신+', '병신', text)
        
        replacements = {
            "^^ㅣ": "씨",
            "^ㅣ": "씨",
            "ㅂㅅ": "병신",
            "ㅅㅂ": "시발",
            "ㄲㅈ": "꺼져",
            "ssi": "씨",
            "c8": "시발",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _check_text_for_profanity(self, text: str) -> bool:
        for i, char in enumerate(text):
            if self._is_noise(char):
                continue
            current = self._root
            for j in range(i, len(text)):
                current_char = text[j]
                if j > i and self._is_noise(current_char):
                    continue
                next_node = current.children.get(current_char)
                if not next_node:
                    break
                current = next_node
                if current.is_end_of_word:
                    return True
        return False

    def contains_profanity(self, text: str) -> bool:
        if not text:
            return False
        qwerty_converted = self.convert_qwerty_to_hangul(text)
        roman_converted = self.convert_roman_to_hangul(text)
        normalized_original = self.normalize_text(text)
        normalized_qwerty = self.normalize_text(qwerty_converted)
        normalized_roman = self.normalize_text(roman_converted)
        return (
            self._check_text_for_profanity(normalized_original)
            or self._check_text_for_profanity(normalized_qwerty)
            or self._check_text_for_profanity(normalized_roman)
        )


class TrieStage:
    def __init__(self, bad_words: Iterable[str] | None = None):
        self.bad_words = list(
            bad_words or ["시발", "씨발", "병신", "개새", "미친", "존나", "지랄"]
        )
        self.filter_trie = ProfanityFilterTrie(self.bad_words)

    def evaluate(self, text: str) -> StageResult:
        has_profanity = self.filter_trie.contains_profanity(text)
        if has_profanity:
            return StageResult(
                is_inappropriate=True,
                label="비속어 패턴",
                reason="1단계(Trie): 명시적 비속어 패턴이 감지되었습니다.",
                details={"badWords": self.bad_words},
            )
        return StageResult(
            is_inappropriate=False,
            label="통과",
            reason="1단계(Trie): 명시적 비속어 패턴이 감지되지 않았습니다.",
            details={},
        )